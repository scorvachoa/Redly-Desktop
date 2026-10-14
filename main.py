"""CLI de Red.ly (descargador de TikTok).

Uso::

    python main.py
    python main.py --debug
    python main.py --url "https://www.tiktok.com/@usuario/video/123"
    python main.py --profile "@usuario" --max 10
    python main.py --batch lista.txt --delay 2
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from app import __version__, config
from app.batch import (
    BatchItemResult,
    BatchReport,
    BatchRunner,
    Manifest,
    item_output_path,
    join_output_dir,
    summarize,
)
from app.client import (
    input_to_profile_url,
    is_profile_url,
    is_tiktok_url,
    normalize_tiktok_url,
    profile_folder_name,
)
from app.downloader import DownloadManager, build_filename
from app.exceptions import TikVidError
from app.models import DownloadOption, ProfileVideo, TikTokResult
from app.savetik import SavetikSource, anonymous_key, search_any

console = Console()

BANNER = """
╭────────────────────────────────────────╮
│            Red.ly  (CLI)              │
╰────────────────────────────────────────╯
"""


class _RedactingFilter(logging.Filter):
    """Evita que tokens o cookies completos lleguen a los logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = config.mask_url(message)
        record.args = ()
        return True


def setup_logging(debug: bool = False) -> None:
    """Configura logging con máscara de tokens; sólo detalla en ``--debug``."""
    level = logging.DEBUG if debug else logging.WARNING
    handler = RichHandler(
        console=console, show_time=debug, show_path=debug, rich_tracebacks=debug
    )
    handler.addFilter(_RedactingFilter())
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[handler],
        force=True,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Cliente de escritorio del flujo HTTP de TikVid para URLs de TikTok (Red.ly).",
    )
    parser.add_argument("--url", help="URL de TikTok (si no se indica, se pide por teclado).")
    parser.add_argument(
        "--profile",
        metavar="PERFIL",
        help="URL de perfil o @usuario: lista sus vídeos y los descarga en lote.",
    )
    parser.add_argument(
        "--batch",
        type=Path,
        metavar="FICHERO",
        help="Fichero con una URL de TikTok (vídeo o perfil) por línea.",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=config.BATCH_DEFAULT_MAX_VIDEOS,
        help="Máximo de vídeos a descargar en el lote (0 = sin límite).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=config.BATCH_DELAY_SECONDS,
        help=f"Pausa en segundos entre vídeos del lote (por defecto: {config.BATCH_DELAY_SECONDS}).",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Sólo lista los vídeos del lote, sin descargar nada.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="No pide confirmación antes del lote.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Descarga de nuevo los vídeos ya marcados como completados.",
    )
    parser.add_argument(
        "--option",
        type=int,
        help="Índice de la opción de descarga a utilizar (1-based).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=f"Carpeta de descargas (por defecto: {config.DOWNLOAD_DIRECTORY}).",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        help="Muestra las opciones y resuelve la URL temporal sin descargar.",
    )
    parser.add_argument("--debug", action="store_true", help="Activa el modo debug.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def ask_url() -> str:
    console.print("[bold]Introduce una URL de TikTok:[/bold]")
    return console.input("> ").strip()


def show_result(result: TikTokResult) -> None:
    console.print("\n[bold]Título:[/bold]")
    console.print(result.title or "(no disponible)")
    console.print("\n[bold]ID:[/bold]")
    console.print(result.video_id or "(no disponible)")
    console.print("\n[bold]Thumbnail:[/bold]")
    console.print(result.thumbnail or "(no disponible)")

    table = Table(title="Opciones", show_lines=False)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Nombre")
    table.add_column("Formato")
    table.add_column("Calidad")
    for index, option in enumerate(result.downloads, start=1):
        table.add_row(
            str(index),
            option.name,
            option.format or "-",
            option.quality or "-",
        )
    console.print()
    console.print(table)


def ask_option(result: TikTokResult) -> DownloadOption:
    count = len(result.downloads)
    while True:
        raw = console.input(f"\n[bold]Selecciona una opción (1-{count}):[/bold] ").strip()
        if raw.lower() in {"q", "salir", "exit"}:
            raise KeyboardInterrupt
        if raw.isdigit() and 1 <= int(raw) <= count:
            return result.downloads[int(raw) - 1]
        console.print("[red]Opción no válida.[/red]")


def select_option(result: TikTokResult, requested: int | None) -> DownloadOption:
    if requested is not None:
        if not 1 <= requested <= len(result.downloads):
            raise TikVidError(
                f"La opción {requested} no existe (hay {len(result.downloads)})."
            )
        return result.downloads[requested - 1]
    return ask_option(result)


def print_debug_search(result: TikTokResult, source: str = "savetikpro→tikvid") -> None:
    console.print("[dim]--- debug ---[/dim]")
    console.print(f"fuente        : {source}")
    console.print(f"endpoint      : {config.SAVETIK_AJAX_SEARCH_URL}")
    console.print(f"fallback      : {config.TIKVID_AJAX_SEARCH_URL}")
    console.print(f"opciones      : {len(result.downloads)}")
    console.print("[dim]--------------[/dim]")


def resolve_with_feedback(manager: DownloadManager, url: str):
    console.print("\n[bold]Resolviendo URL...[/bold]")
    info = manager.resolve_url(url)
    console.print("✓ URL temporal encontrada")
    if info.redirected:
        console.print("✓ Redirect detectado")
        console.print(f"✓ CDN encontrado: [cyan]{config.url_host(info.final_url) or '?'}[/cyan]")
    else:
        console.print("· Sin redirect (URL directa de CDN)")
    if info.status_code is not None:
        console.print(f"· HTTP {info.status_code}")
    return info


def pick_auto_option(result: TikTokResult) -> DownloadOption:
    """Selección automática para el lote: mejor MP4 (HD si existe)."""
    mp4s = [o for o in result.downloads if (o.format or "").lower() == "mp4"]
    candidates = mp4s or result.downloads
    for option in candidates:
        if (option.quality or "").lower() == "hd":
            return option
    return candidates[0]


def video_url_to_item(url: str, source: SavetikSource) -> ProfileVideo:
    """Convierte una URL de vídeo suelto en un elemento de lote."""
    result = search_any(url, savetik=source)
    option = pick_auto_option(result)
    item_id = result.video_id or anonymous_key(option.url)
    return ProfileVideo(
        id=item_id,
        title=result.title,
        url=option.url,
        format=(option.format or "mp4").lower(),
    )


def collect_targets(args: argparse.Namespace) -> tuple[list[ProfileVideo], Manifest, int]:
    """Une perfiles y vídeos sueltos en una cola; devuelve (cola, manifiesto, omitidos)."""
    source = SavetikSource(debug=args.debug)
    profile_targets: list[str] = []
    video_targets: list[str] = []

    if args.profile:
        profile_targets.append(input_to_profile_url(args.profile))
    if args.batch:
        for line in args.batch.read_text(encoding="utf-8").splitlines():
            entry = normalize_tiktok_url(line.strip())
            if not entry or entry.startswith("#"):
                continue
            if not is_tiktok_url(entry):
                console.print(f"[yellow]· Línea ignorada (no es TikTok): {entry}[/yellow]")
                continue
            if is_profile_url(entry):
                profile_targets.append(entry)
            else:
                video_targets.append(entry)
    if args.url and is_tiktok_url(args.url):
        entry = normalize_tiktok_url(args.url)
        if is_profile_url(entry):
            profile_targets.append(entry)
        elif not args.batch:
            video_targets.append(entry)

    limit = args.max if args.max and args.max > 0 else None
    resume = not args.no_resume
    download_dir = args.output_dir or config.DOWNLOAD_DIRECTORY
    manifest = Manifest(Path(download_dir) / "manifest.json")
    if resume:
        manifest.load()

    def _is_done(subdir: str | None, item: ProfileVideo) -> bool:
        if not resume:
            return False
        target = item_output_path(join_output_dir(download_dir, subdir), item)
        return manifest.is_done(item.id, target)

    items: list[ProfileVideo] = []
    skipped_total = 0

    def _on_page(page: int, pending: int, skipped: int) -> None:
        console.print(
            f"  · página {page}: {pending} pendiente(s) · {skipped} ya descargado(s)",
            style="dim",
        )

    for profile in profile_targets:
        remaining = None if limit is None else limit - len(items)
        if remaining is not None and remaining <= 0:
            break
        subdir = profile_folder_name(profile)
        console.print(f"\n[bold]Listando perfil...[/bold] {profile}")
        if subdir:
            console.print(f"  · destino: {join_output_dir(download_dir, subdir)}", style="dim")
        try:
            listed, skipped, _pages = source.list_profile_pending(
                profile,
                lambda item, _subdir=subdir: _is_done(_subdir, item),
                max_pending=remaining,
                on_page=_on_page,
            )
            for item in listed:
                items.append(item.model_copy(update={"subdir": subdir}))
            skipped_total += skipped
        except TikVidError as exc:
            console.print(f"[red]✗ No se pudo listar {profile}: {exc}[/red]")

    for video in video_targets:
        remaining = None if limit is None else limit - len(items)
        if remaining is not None and remaining <= 0:
            break
        console.print(f"\n[bold]Analizando vídeo...[/bold] {video}")
        try:
            item = video_url_to_item(video, source)
            if _is_done(None, item):
                skipped_total += 1
                console.print(f"  · ya descargado, se omite: {item.id}", style="dim")
                continue
            items.append(item)
        except TikVidError as exc:
            console.print(f"[red]✗ No se pudo analizar {video}: {exc}[/red]")

    if skipped_total > 0:
        console.print(
            f"  · {skipped_total} ya descargado(s) se omiten de la cola", style="dim"
        )

    return items, manifest, skipped_total


def show_profile_table(items: list[ProfileVideo]) -> None:
    table = Table(title=f"Vídeos del perfil ({len(items)})", show_lines=False)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("ID", style="dim")
    table.add_column("Título")
    table.add_column("Canal")
    table.add_column("Vistas", justify="right")
    table.add_column("Carpeta", style="dim")
    for index, item in enumerate(items, start=1):
        table.add_row(
            str(index),
            item.id,
            item.display_name(),
            item.channel or "-",
            item.views or "-",
            item.subdir or "-",
        )
    console.print()
    console.print(table)


def run_batch(
    args: argparse.Namespace,
    items: list[ProfileVideo],
    manifest: Manifest,
    skipped: int = 0,
) -> int:
    if not items:
        if skipped > 0:
            console.print(
                f"[yellow]No hay vídeos pendientes: los {skipped} ya estaban descargados.[/yellow]"
            )
            console.print("[dim]Usa --no-resume para volver a descargarlos.[/dim]")
            return 0
        console.print("[red]No hay vídeos que descargar.[/red]")
        return 1

    show_profile_table(items)

    if args.list_only:
        console.print("\n[yellow]Listado terminado (--list-only).[/yellow]")
        return 0

    if not args.yes:
        download_dir = args.output_dir or config.DOWNLOAD_DIRECTORY
        subdirs = sorted({item.subdir for item in items if item.subdir})
        if subdirs:
            layout = ", ".join(str(join_output_dir(download_dir, s)) for s in subdirs)
        else:
            layout = str(download_dir)
        console.print(
            f"\n[bold]Se descargarán {len(items)} vídeo(s)[/bold] "
            f"en [cyan]{layout}[/cyan] (pausa {args.delay:g}s entre vídeos)."
        )
        answer = console.input("¿Continuar? [s/N]: ").strip().lower()
        if answer not in {"s", "si", "sí", "y", "yes"}:
            console.print("[yellow]Cancelado.[/yellow]")
            return 0

    manager = DownloadManager(download_dir=args.output_dir, debug=args.debug)

    def on_item_start(index: int, total: int, item: ProfileVideo) -> None:
        console.print(f"[cyan]{index}/{total}[/cyan] {item.display_name()}")

    def on_item_done(index: int, total: int, result: BatchItemResult) -> None:
        if result.state == "ok":
            console.print(f"      [green]✓ {result.file} ({result.bytes:,} bytes)[/green]")
        elif result.state == "skipped":
            console.print(f"      [dim]↷ ya descargado: {result.file}[/dim]")
        else:
            console.print(f"      [red]✗ {result.error}[/red]")

    runner = BatchRunner(
        manager,
        manifest,
        delay=args.delay,
        resume=not args.no_resume,
        on_item_start=on_item_start,
        on_item_done=on_item_done,
    )

    console.print("\n[bold]Descargando...[/bold]")
    try:
        report = runner.run(items)
    except KeyboardInterrupt:
        console.print("\n[yellow]Lote interrumpido (el manifiesto conserva el progreso).[/yellow]")
        return 130

    _print_batch_summary(report, manifest, args.output_dir)
    return 0 if report.failed == 0 else 1


def _print_batch_summary(
    report: BatchReport, manifest: Manifest, output_dir: Path | None = None
) -> None:
    console.print(f"\n[bold green]✓ Lote terminado[/bold green] · {summarize(report)}")
    console.print(f"[dim]Manifiesto: {manifest.path}[/dim]")
    if report.errors:
        console.print("[yellow]Reejecuta el mismo comando para reintentar los fallos.[/yellow]")
        for failure in report.errors:
            console.print(f"  [red]✗ {failure.key}[/red]: {failure.error}")


def run(args: argparse.Namespace) -> int:
    batch_mode = bool(args.profile or args.batch)
    url = (args.url or "").strip()
    if url and not batch_mode and is_profile_url(url):
        batch_mode = True

    if batch_mode:
        items, manifest, skipped = collect_targets(args)
        return run_batch(args, items, manifest, skipped)

    if not url:
        url = ask_url()

    if not is_tiktok_url(url):
        console.print("[red]La URL no parece ser una URL válida de TikTok.[/red]")
        return 1

    if is_profile_url(url):
        args.url = url
        items, manifest, skipped = collect_targets(args)
        return run_batch(args, items, manifest, skipped)

    console.print("\n[bold]Analizando vídeo...[/bold]")
    console.print("✓ URL válida")

    result = search_any(url, debug=args.debug)
    console.print("✓ Información obtenida")
    if args.debug:
        print_debug_search(result)

    show_result(result)
    option = select_option(result, args.option)

    manager = DownloadManager(download_dir=args.output_dir, debug=args.debug)
    info = resolve_with_feedback(manager, option.url)

    if args.no_download:
        console.print("\n[yellow]Descarga omitida (--no-download).[/yellow]")
        return 0

    filename = build_filename(
        video_id=result.video_id,
        quality=option.quality,
        file_format=option.format,
    )
    target = manager.download(info.final_url, manager.download_dir / filename)

    console.print("\n[bold green]✓ Descarga completada[/bold green]")
    console.print(f"\n[bold]Archivo:[/bold]\n{target}")
    return 0


def _configure_streams() -> None:
    """Evita mojibake de acentos al redirigir la salida a fichero/pipe."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_streams()
    args = build_arg_parser().parse_args(argv)
    setup_logging(args.debug)
    console.print(BANNER)
    try:
        return run(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Operación cancelada.[/yellow]")
        return 130
    except TikVidError as exc:
        console.print(f"\n[red]✗ {exc}[/red]")
        if args.debug:
            console.print_exception()
        return 1


if __name__ == "__main__":
    sys.exit(main())
