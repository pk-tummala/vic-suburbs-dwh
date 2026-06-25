"""Extractors: pull a configured source into the landing Volume.

One generic entrypoint (``run_extract``) selects a connector from the entity's source
config (``config/sources/<entity>.yaml``) and writes raw files stamped with one
``batch_id``. The synthetic connector delegates to the generator; the CKAN and ABS
connectors call the live public APIs (requiring network access and, for DataVic, an API
key supplied via the ``DATAVIC_API_KEY`` secret).
"""

from __future__ import annotations

import argparse
import os
from abc import ABC, abstractmethod
from pathlib import Path

from vic_suburbs.common.config import load_source
from vic_suburbs.common.lineage import new_batch_id


class Extractor(ABC):
    """Connector interface. Implementations write files into ``landing/<entity>/``."""

    def __init__(self, entity: str, source_cfg: dict, landing: Path, batch_id: str):
        self.entity = entity
        self.cfg = source_cfg
        self.landing = landing
        self.batch_id = batch_id

    @abstractmethod
    def extract(self) -> list[Path]: ...

    def _out_dir(self) -> Path:
        d = self.landing / self.cfg.get("landing_path", self.entity).rstrip("/")
        d.mkdir(parents=True, exist_ok=True)
        return d


class CkanExtractor(Extractor):
    """DataVic (CKAN) datastore_search paginated pull -> CSV."""

    def extract(self) -> list[Path]:
        import csv

        import requests

        ck = self.cfg["ckan"]
        resource_id = ck["resource_id"]
        if resource_id.startswith("REPLACE_"):
            raise RuntimeError(
                f"[{self.entity}] resource_id not pinned in config/sources/{self.entity}.yaml"
            )
        api_key = os.environ.get("DATAVIC_API_KEY")
        headers = {"apikey": api_key} if api_key else {}
        url = f"{ck['base_url']}/api/3/action/datastore_search"
        offset, page_size = 0, ck.get("page_size", 1000)
        rows: list[dict] = []
        while True:
            resp = requests.get(
                url,
                params={"resource_id": resource_id, "limit": page_size, "offset": offset},
                headers=headers,
                timeout=60,
            )
            resp.raise_for_status()
            records = resp.json()["result"]["records"]
            if not records:
                break
            rows.extend(records)
            offset += page_size
            if len(records) < page_size:
                break
        out = self._out_dir() / f"{self.entity}_{self.batch_id[:8]}.csv"
        if rows:
            with open(out, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
        return [out]


class AbsExtractor(Extractor):
    """ABS SDMX / geography pull. Stub: wire the dataflow query per entity at build time."""

    def extract(self) -> list[Path]:
        raise NotImplementedError(
            f"[{self.entity}] ABS connector: implement the SDMX/geography query for "
            f"dataflow {self.cfg.get('abs', {}).get('dataflow', '<unset>')}."
        )


class SyntheticExtractor(Extractor):
    """Delegate to the generator: ensure the universe exists, then emit a batch."""

    def extract(self) -> list[Path]:
        from vic_suburbs.generator import emit, seed

        db = "synthetic_universe.db"
        if not Path(db).exists():
            seed.build_universe("config/synthetic/seed_config.yaml", db)
        return emit.emit(
            mode="mixed",
            landing=str(self.landing),
            db_path=db,
            mutation_config="config/synthetic/mutation_rules.yaml",
        )


CONNECTORS = {
    "ckan": CkanExtractor,
    "abs_sdmx": AbsExtractor,
    "abs_geography": AbsExtractor,
    "synthetic": SyntheticExtractor,
}


def run_extract(entity: str, landing: str, batch_id: str | None = None) -> list[Path]:
    cfg = load_source(entity)
    connector = cfg["connector"]
    if connector not in CONNECTORS:
        raise KeyError(f"Unknown connector {connector!r}; known: {sorted(CONNECTORS)}")
    extractor = CONNECTORS[connector](entity, cfg, Path(landing), batch_id or new_batch_id())
    return extractor.extract()


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(description="Extract a source entity into landing.")
    ap.add_argument("entity")
    ap.add_argument("--landing", default=".local/landing")
    args = ap.parse_args()
    written = run_extract(args.entity, args.landing)
    print(f"{args.entity}: wrote {len(written)} file(s)")


if __name__ == "__main__":
    main()
