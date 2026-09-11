"""
A fly's genome.

Every fly in the swarm runs the SAME wiring - the male CNS connectome is a
measurement and is never touched. What differs between flies is the one thing
electron microscopy cannot see: synaptic efficacy. So a genome is a vector of
gains, one per cell type, exactly the free parameter of Lappalainen et al. 2024
and of flycoin/train.py.

Only cell types on a path between the market senses and the command neurons
are allowed to vary (train.relevant_types); everything else stays at 1.0, so
a child differs from its parent in a few hundred dimensions, not twelve
thousand.

The founder genome is all ones: raw anatomy. flycoin measured that trained
gains do not beat raw anatomy on unseen noise, so evolution starts from the
measurement and selection (who earns, who starves) does the rest.
"""
import hashlib
import numpy as np


class Genome:
    __slots__ = ("gains", "free", "gen", "parents")

    def __init__(self, gains, free, gen=0, parents=()):
        self.gains = np.asarray(gains, dtype=np.float32)
        self.free = np.asarray(free, dtype=np.int64)   # indices allowed to vary
        self.gen = int(gen)
        self.parents = tuple(parents)

    @classmethod
    def founder(cls, n_types, free):
        return cls(np.ones(n_types, dtype=np.float32), free, gen=0, parents=())

    @property
    def gid(self):
        """Short content hash: the genome's name."""
        return hashlib.sha256(self.gains.tobytes()).hexdigest()[:12]

    def mutate(self, rng, sigma):
        g = self.gains.copy()
        noise = rng.normal(0.0, sigma, size=len(self.free)).astype(np.float32)
        g[self.free] = np.clip(g[self.free] * np.exp(noise), 0.05, 20.0)
        return Genome(g, self.free, gen=self.gen + 1, parents=(self.gid,))

    def cross(self, other, rng, frac, sigma):
        """Take `frac` of the free types from `other`, then mutate."""
        g = self.gains.copy()
        take = rng.random(len(self.free)) < frac
        idx = self.free[take]
        g[idx] = other.gains[idx]
        child = Genome(g, self.free, gen=max(self.gen, other.gen) + 1,
                       parents=(self.gid, other.gid))
        return child.mutate(rng, sigma) if sigma > 0 else child

    def distance(self, other):
        """Mean |log gain ratio| over the free types: how far two flies have drifted."""
        a = np.log(self.gains[self.free]); b = np.log(other.gains[self.free])
        return float(np.mean(np.abs(a - b)))

    def summary(self):
        f = self.gains[self.free]
        return {"gid": self.gid, "gen": self.gen, "parents": list(self.parents),
                "free": int(len(self.free)),
                "mean_gain": float(f.mean()), "spread": float(np.log(f).std())}

    def save(self, path):
        np.savez_compressed(path, gains=self.gains, free=self.free,
                            gen=np.int64(self.gen), parents=np.array(self.parents, dtype="U12"))

    @classmethod
    def load(cls, path):
        z = np.load(path, allow_pickle=False)
        return cls(z["gains"], z["free"], gen=int(z["gen"]), parents=tuple(str(p) for p in z["parents"]))
