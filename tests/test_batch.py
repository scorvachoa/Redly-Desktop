"""Tests del motor de lote (BatchRunner/Manifest) con un gestor falso."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import config
from app.batch import BatchRunner, Manifest, summarize
from app.exceptions import TikVidDownloadError
from app.models import ProfileVideo


def make_items(count: int = 3) -> list[ProfileVideo]:
    return [
        ProfileVideo(
            id=str(7000000000000000000 + index),
            title=f"Vídeo {index}",
            url=f"https://pro.snapcdn.app/dl?token=FAKE_{7000000000000000000 + index}",
            format="mp4",
        )
        for index in range(count)
    ]


class FakeManager:
    """Gestor falso que escribe archivos pequeños en disco."""

    def __init__(self, download_dir: Path, fail_ids: set[str] | None = None) -> None:
        self.download_dir = Path(download_dir)
        self.fail_ids = fail_ids or set()
        self.calls: list[str] = []

    def resolve_url(self, url: str):
        from app.models import RedirectInfo

        return RedirectInfo(
            original_url=url, final_url=url, redirected=True, status_code=200, hops=1
        )

    def download(self, url, output_path, *, show_progress=False, on_progress=None):
        video_id = url.rsplit("FAKE_", 1)[-1]
        if video_id in self.fail_ids:
            raise TikVidDownloadError("fallo simulado")
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = b"x" * 2048
        target.write_bytes(payload)
        self.calls.append(target.name)
        if on_progress:
            on_progress(len(payload), len(payload))
        return target


@pytest.fixture()
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config, "MANIFEST_PATH", tmp_path / "manifest.json")
    monkeypatch.setattr(config, "BATCH_DELAY_SECONDS", 0)
    return tmp_path


def test_descarga_todo_y_escribe_manifiesto(workdir: Path) -> None:
    manager = FakeManager(workdir / "out")
    runner = BatchRunner(manager, Manifest(workdir / "manifest.json"), delay=0)

    report = runner.run(make_items(2))

    assert report.downloaded == 2
    assert report.failed == 0
    assert (workdir / "out" / "Redly_7000000000000000000.mp4").is_file()

    manifest_path = workdir / "manifest.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert raw["items"]["7000000000000000000"]["state"] == "ok"
    assert raw["items"]["7000000000000000000"]["file"].endswith(".mp4")
    # §14: el manifiesto no debe contener URLs ni tokens
    content = manifest_path.read_text(encoding="utf-8")
    assert "http" not in content.lower()
    assert "token" not in content.lower()


def test_reanudacion_omite_los_ok(workdir: Path) -> None:
    manager = FakeManager(workdir / "out")
    manifest = Manifest(workdir / "manifest.json")
    runner = BatchRunner(manager, manifest, delay=0)
    items = make_items(2)

    runner.run(items)
    report2 = BatchRunner(FakeManager(workdir / "out"), manifest, delay=0).run(items)

    assert report2.downloaded == 0
    assert report2.skipped == 2


def test_reanudacion_reintenta_errores(workdir: Path) -> None:
    failing = FakeManager(workdir / "out", fail_ids={"7000000000000000001"})
    manifest = Manifest(workdir / "manifest.json")
    report1 = BatchRunner(failing, manifest, delay=0).run(make_items(2))

    assert report1.failed == 1
    assert report1.downloaded == 1

    report2 = BatchRunner(FakeManager(workdir / "out"), manifest, delay=0).run(
        make_items(2)
    )

    assert report2.downloaded == 1
    assert report2.skipped == 1
    assert report2.failed == 0


def test_un_fallo_no_detiene_el_lote(workdir: Path) -> None:
    manager = FakeManager(workdir / "out", fail_ids={"7000000000000000000"})
    runner = BatchRunner(manager, Manifest(workdir / "manifest.json"), delay=0)

    report = runner.run(make_items(3))

    assert report.downloaded == 2
    assert report.failed == 1
    assert report.errors[0].key == "7000000000000000000"
    assert "fallo simulado" in (report.errors[0].error or "")


def test_limit_recorta_la_cola(workdir: Path) -> None:
    manager = FakeManager(workdir / "out")
    runner = BatchRunner(manager, Manifest(workdir / "manifest.json"), delay=0)

    report = runner.run(make_items(5), limit=2)

    assert len(report.results) == 2


def test_delay_entre_descargas(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("app.batch.time.sleep", sleeps.append)

    manager = FakeManager(workdir / "out")
    runner = BatchRunner(manager, Manifest(workdir / "manifest.json"), delay=1.5)

    runner.run(make_items(3))

    assert sleeps == [1.5, 1.5]


def test_callbacks_reciben_eventos(workdir: Path) -> None:
    events: list[str] = []
    manager = FakeManager(workdir / "out")
    runner = BatchRunner(
        manager,
        Manifest(workdir / "manifest.json"),
        delay=0,
        on_item_start=lambda index, total, item: events.append(f"start:{index}/{total}"),
        on_item_progress=lambda done, total: events.append(f"bytes:{done}"),
        on_item_done=lambda index, total, result: events.append(f"done:{result.state}"),
    )

    runner.run(make_items(1))

    assert events[0] == "start:1/1"
    assert "bytes:2048" in events
    assert events[-1] == "done:ok"


def test_no_resume_repite_la_descarga(workdir: Path) -> None:
    manager = FakeManager(workdir / "out")
    manifest = Manifest(workdir / "manifest.json")
    BatchRunner(manager, manifest, delay=0).run(make_items(1))

    manager2 = FakeManager(workdir / "out")
    report = BatchRunner(manager2, manifest, delay=0, resume=False).run(make_items(1))

    assert report.downloaded == 1
    assert len(manager2.calls) == 1


def test_resume_sin_archivo_reintenta(workdir: Path) -> None:
    manager = FakeManager(workdir / "out")
    manifest = Manifest(workdir / "manifest.json")
    BatchRunner(manager, manifest, delay=0).run(make_items(1))

    (workdir / "out" / "Redly_7000000000000000000.mp4").unlink()

    report = BatchRunner(FakeManager(workdir / "out"), manifest, delay=0).run(
        make_items(1)
    )

    assert report.downloaded == 1


def test_manifest_ilegible_empieza_de_cero(workdir: Path) -> None:
    path = workdir / "manifest.json"
    path.write_text("{{{ no es json", encoding="utf-8")

    manifest = Manifest(path)

    assert manifest.get("x") is None


def test_summarize() -> None:
    from app.batch import BatchItemResult, BatchReport

    report = BatchReport(
        results=[
            BatchItemResult(key="a", state="ok"),
            BatchItemResult(key="b", state="skipped"),
            BatchItemResult(key="c", state="error", error="x"),
        ]
    )

    assert summarize(report) == "1 descargado(s) · 1 omitido(s) · 1 fallo(s)"
