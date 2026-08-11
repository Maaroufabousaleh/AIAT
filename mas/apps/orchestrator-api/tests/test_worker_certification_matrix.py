import importlib.util
from pathlib import Path


def _matrix_module():
    mas_root = Path(__file__).resolve().parents[3]
    path = mas_root / "scripts" / "generate_worker_certification_matrix.py"
    spec = importlib.util.spec_from_file_location("worker_matrix", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, mas_root


def test_worker_certification_matrix_is_deterministic_and_explicit_about_pending_evidence():
    module, mas_root = _matrix_module()
    matrix = module.generate(workers_dir=mas_root / "workers")

    assert matrix["errors"] == []
    assert len(matrix["workers"]) == 39
    assert matrix["programme_scope"] == "personal-internal-only"
    assert matrix["license_handling"] == "metadata-only"
    rows = {row["worker_id"]: row for row in matrix["workers"]}
    assert rows["coding_worker"]["evidence_state"] == "pending_security_evidence"
    assert rows["tester"]["evidence_state"] == "pending_security_evidence"
    assert rows["ceo"]["evidence_state"] == "pending_live_certification"


def test_checked_in_worker_certification_matrix_matches_generator():
    module, mas_root = _matrix_module()
    generated = module._render(module.generate(workers_dir=mas_root / "workers"))
    checked_in = (mas_root / "docs/provenance/worker_certification_matrix.yaml").read_text(
        encoding="utf-8"
    )
    assert checked_in == generated
