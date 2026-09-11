"""
The neuron cloud the site draws.

Positions are measured soma coordinates from the EM reconstruction (flycoin's
body-annotations.feather), for every neuron that has one. Nothing is
synthetic. Written once:

  build/cloud.npz        keep (indices into the 165k population), xyz, group,
                         thumb (indices into keep for the small brains)
  site/web/neurons.bin   little-endian: uint32 N, uint32 T, float32 xyz[N*3],
                         uint8 group[N], uint32 thumb[T]

Groups (for colour on the site):
  0 other   1 vision (L1/L2)   2 descending   3 MN9 eat   4 DNp01 escape
  5 pC1 court   6 market senses (LB*, LPLC2, ORN_DA1)   7 DNp09 freeze

  py -m colony.cloud
"""
import struct
import sys
import numpy as np

from .config import FLYCOIN, BUILD, ROOT

GROUPS = (
    (r"^L1$|^L2$", 1),
    (r"^DNp09$", 7),
    (r"^(LB3c|LB1e|LB4a|LB1d|LB3b|LB1c|LPLC2|ORN_DA1)$", 6),
    (r"^pC1", 5),
    (r"^DNp01$", 4),
    (r"^MN9$", 3),
)


def build(n_thumb=6000, seed=0):
    import pandas as pd
    if str(FLYCOIN) not in sys.path:
        sys.path.insert(0, str(FLYCOIN))
    from flysim import FlyBrain
    fb = FlyBrain(FLYCOIN / "build" / "graph.npz")
    a = pd.read_feather(FLYCOIN / "data" / "body-annotations.feather")
    a = a[["bodyId", "somaLocation"]].dropna(subset=["somaLocation"]).drop_duplicates(subset=["bodyId"])
    pos = pd.Series(list(a.somaLocation), index=a.bodyId.to_numpy())
    have = pos.reindex(fb.bodies)
    ok = have.notna().to_numpy()
    keep = np.flatnonzero(ok)
    P = np.stack(have[ok].to_numpy()).astype(np.float64)
    # centre and scale to a unit-ish radius; the browser never sees nanometres
    P -= P.mean(axis=0)
    # principal axes: longest extent along x, second along y, so the browser sees it edge-on
    _, _, vt = np.linalg.svd(P[::7], full_matrices=False)
    P = P @ vt.T
    ext = np.percentile(np.abs(P), 99.5, axis=0)
    P = P[:, np.argsort(-ext)]          # longest extent on x, then y, then z
    P /= ext.max()
    xyz = P.astype(np.float32)
    print("extent x/y/z", np.round(np.percentile(np.abs(P), 99.5, axis=0), 2).tolist())

    group = np.zeros(fb.n, dtype=np.uint8)
    for rx, g in GROUPS:
        group[fb.where(type_re=rx)] = g
    grp = group[keep]

    rng = np.random.default_rng(seed)
    dn = fb.where(superclass="descending_neuron")
    group2 = group.copy(); group2[dn] = np.maximum(group2[dn], 2)
    grp = group2[keep]
    special = np.flatnonzero(grp > 0)
    rest = np.flatnonzero(grp == 0)
    take = rng.choice(rest, size=max(0, min(n_thumb - len(special), len(rest))), replace=False)
    thumb = np.sort(np.concatenate([special, take])).astype(np.uint32)

    BUILD.mkdir(exist_ok=True)
    np.savez_compressed(BUILD / "cloud.npz", keep=keep.astype(np.int64), xyz=xyz, group=grp, thumb=thumb)
    out = ROOT / "site" / "web" / "neurons.bin"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        f.write(struct.pack("<II", len(keep), len(thumb)))
        f.write(np.ascontiguousarray(xyz).tobytes())
        f.write(np.ascontiguousarray(grp).tobytes())
        f.write(np.ascontiguousarray(thumb).tobytes())
    print(f"cloud: {len(keep):,} neurons with soma positions of {fb.n:,}; thumbs {len(thumb):,}; "
          f"groups {np.bincount(grp, minlength=8).tolist()} -> {out} ({out.stat().st_size/1e6:.1f} MB)")
    return keep


def load_keep():
    p = BUILD / "cloud.npz"
    if not p.exists():
        return None
    return np.load(p, allow_pickle=False)["keep"]


if __name__ == "__main__":
    build()
