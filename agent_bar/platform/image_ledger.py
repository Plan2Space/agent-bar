"""image_ledger.py — registro obrigatorio de toda imagem que o Studio entrega a um agente.

Decisao do Felipe (VISUAL-STUDIO Fase 1) e SPEC P2S-SUITE01-VISUAL-CONVERGENCE S01C-R3: cada imagem (referencia,
avaliacao, pacote de correcao, anexo do Chat) e registrada ANTES de ir ao provedor, com quem recebe (provedor, modelo,
sessao), qual imagem (caminho, sha256 dos bytes enviados), quando (UTC), para que e quem pediu.

Regras:
- **registrar antes de enviar**: `send_with_images` le e valida as imagens, grava o lote e so entao chama o
  adaptador, com os MESMOS bytes que foram hasheados;
- **falha de persistencia = erro explicito** (`ImageLedgerError`) e nada e enviado: registro fechado, banco travado,
  erro de SQLite, campo obrigatorio vazio, finalidade desconhecida, hash da referencia diferente do arquivo;
- o lote e tudo ou nada (uma transacao); depois do envio a linha passa a SENT, e a NOT_SENT com o motivo quando o
  adaptador recusa (ex.: modelo sem `vision`);
- nao e o Permission Inbox: e uma tabela propria (`image_deliveries`) no mesmo arquivo SQLite, conexao propria (WAL);
- **sem terceiro registro de referencia**: os campos de `ReferenceLink` apontam para o catalogo da design-library
  (`reference_id` e a chave de la); o `source_hash` informado e conferido contra o sha256 calculado aqui.

Mapeamento S01C-R3 (Anexo A §5 -> coluna): reference_id -> ref_id; source_hash -> sha256 (calculado; o informado so
confere); source_type -> ref_source_type; project_id -> ref_project_id; room_id -> ref_room_id; created_at ->
ref_created_at (da referencia; a entrega tem recorded_at); original_file -> ref_original_file; source_manifest ->
ref_source_manifest; build_spec_id -> ref_build_spec_id; reference_version -> ref_version.
"""
from __future__ import annotations

import concurrent.futures as cf
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

from agent_bar.platform.adapter import AdapterError, ImagePayload, ProviderAdapter, load_images

log = logging.getLogger("agent_bar.platform.image_ledger")

PURPOSES = ("REFERENCE", "EVALUATION", "CORRECTION_PACKAGE", "CHAT")
STATUSES = ("RECORDED", "SENT", "NOT_SENT")
SCHEMA_VERSION = 1


class ImageLedgerError(AdapterError):
    """O registro da imagem falhou; por regra, a imagem nao foi enviada."""

    code = "IMAGE_LEDGER_FAILED"


@dataclass(frozen=True)
class ReferenceLink:
    """Liga a entrega a uma referencia do catalogo da design-library (S01C-R3). So `reference_id` e obrigatorio."""

    reference_id: str
    reference_version: str = ""
    source_type: str = ""
    source_hash: str = ""
    project_id: str = ""
    room_id: str = ""
    original_file: str = ""
    source_manifest: str = ""
    build_spec_id: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class ImageDelivery:
    id: str
    batch_id: str
    recorded_at: str
    recorded_ts: float
    path: str
    sha256: str
    bytes: int
    mime: str
    provider_id: str
    model_id: str
    session_id: str
    provider_session_id: str
    requested_by: str
    purpose: str
    status: str
    error: str
    ref_id: str
    ref_version: str
    ref_source_type: str
    ref_project_id: str
    ref_room_id: str
    ref_original_file: str
    ref_source_manifest: str
    ref_build_spec_id: str
    ref_created_at: str
    role: str = ""                   # papel da imagem no pedido: "reference" | "render" | "" (sem papel declarado)


ROLES = ("", "reference", "render")
_COLUMNS = [f.name for f in fields(ImageDelivery)]


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds")


class ImageLedger:
    def __init__(self, path: Path | str = ":memory:", *, clock: Callable[[], float] = time.time,
                 on_change: Callable[[list[ImageDelivery]], None] | None = None, busy_timeout: float = 5.0):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.on_change = on_change
        self.closed = False
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=busy_timeout)
        self._db.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        cols = ", ".join(f"{c} {'REAL' if c == 'recorded_ts' else 'INTEGER' if c == 'bytes' else 'TEXT'} NOT NULL"
                         + (" DEFAULT ''" if c == "role" else "") for c in _COLUMNS if c != "id")
        self._db.executescript(f"""
            CREATE TABLE IF NOT EXISTS image_deliveries (id TEXT PRIMARY KEY, {cols});
            CREATE INDEX IF NOT EXISTS image_deliveries_session ON image_deliveries(session_id, recorded_ts);
            CREATE INDEX IF NOT EXISTS image_deliveries_sha ON image_deliveries(sha256);
            CREATE INDEX IF NOT EXISTS image_deliveries_batch ON image_deliveries(batch_id);
        """)
        have = {r["name"] for r in self._db.execute("PRAGMA table_info(image_deliveries)")}
        if "role" not in have:                                                # registro de antes do papel: migra
            self._db.execute("ALTER TABLE image_deliveries ADD COLUMN role TEXT NOT NULL DEFAULT ''")

    # ------------------------------------------------------------------------------------------------ gravar
    def record_delivery(self, images: Iterable[ImagePayload | str | Path], *, provider_id: str, model_id: str,
                        session_id: str, requested_by: str, purpose: str, provider_session_id: str = "",
                        reference: ReferenceLink | None = None,
                        roles: Sequence[str] | None = None) -> list[ImageDelivery]:
        """Grava o lote (tudo ou nada) com estado RECORDED. Qualquer falha -> ImageLedgerError.

        `roles` (um por imagem, na ordem): o papel de cada imagem no mesmo pedido, ex.: a referência e o render. Com
        papéis, o `source_hash` da referência é conferido só contra a imagem de papel "reference"."""
        if self.closed:
            raise ImageLedgerError("registro de imagem fechado: nada enviado")
        if purpose not in PURPOSES:
            raise ImageLedgerError(f"finalidade desconhecida {purpose!r} (aceita {list(PURPOSES)})")
        for name, value in (("provider_id", provider_id), ("model_id", model_id), ("session_id", session_id),
                            ("requested_by", requested_by)):
            if not value:
                raise ImageLedgerError(f"campo obrigatorio vazio: {name}")
        payloads = load_images(images)
        if not payloads:
            raise ImageLedgerError("nenhuma imagem para registrar")
        role_of = list(roles) if roles is not None else [""] * len(payloads)
        if len(role_of) != len(payloads) or any(r not in ROLES for r in role_of):
            raise ImageLedgerError(f"papéis {role_of} não casam com {len(payloads)} imagem(ns) (aceita {list(ROLES)})")
        ref = reference or ReferenceLink(reference_id="")
        if reference is not None:
            if not reference.reference_id:
                raise ImageLedgerError("referencia sem reference_id (chave do catalogo da design-library)")
            checked = [p for p, r in zip(payloads, role_of, strict=True) if r == "reference"] \
                if "reference" in role_of else payloads
            if reference.source_hash and any(p.sha256 != reference.source_hash.lower() for p in checked):
                raise ImageLedgerError(f"source_hash da referencia {reference.reference_id} nao confere com o arquivo "
                                       f"entregue ({payloads[0].sha256[:12]}…)")
        batch, ts = uuid.uuid4().hex, self.clock()
        rows = [ImageDelivery(
            id=uuid.uuid4().hex, batch_id=batch, recorded_at=_utc(ts), recorded_ts=ts, path=p.path, sha256=p.sha256,
            bytes=len(p.data), mime=p.mime, provider_id=provider_id, model_id=model_id, session_id=session_id,
            provider_session_id=provider_session_id, requested_by=requested_by, purpose=purpose, status="RECORDED",
            error="", ref_id=ref.reference_id, ref_version=ref.reference_version, ref_source_type=ref.source_type,
            ref_project_id=ref.project_id, ref_room_id=ref.room_id, ref_original_file=ref.original_file,
            ref_source_manifest=ref.source_manifest, ref_build_spec_id=ref.build_spec_id,
            ref_created_at=ref.created_at, role=role) for p, role in zip(payloads, role_of, strict=True)]
        sql = f"INSERT INTO image_deliveries ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})"
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    self._db.executemany(sql, [tuple(getattr(r, c) for c in _COLUMNS) for r in rows])
                    self._db.execute("COMMIT")
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
            except sqlite3.Error as exc:
                raise ImageLedgerError(f"falha ao gravar o registro de imagem ({type(exc).__name__}: {exc}): "
                                       "nada enviado") from exc
        self._changed(rows)
        return rows

    def mark(self, batch_id: str, status: str, error: str = "") -> list[ImageDelivery]:
        if status not in STATUSES:
            raise ValueError(status)
        if self.closed:
            raise ImageLedgerError("registro de imagem fechado")
        with self._lock:
            try:
                self._db.execute("UPDATE image_deliveries SET status=?, error=? WHERE batch_id=?",
                                 (status, error[:500], batch_id))
            except sqlite3.Error as exc:
                raise ImageLedgerError(f"falha ao atualizar o registro ({type(exc).__name__}: {exc})") from exc
        rows = self.batch(batch_id)
        self._changed(rows)
        return rows

    # ------------------------------------------------------------------------------------------------ consulta
    def batch(self, batch_id: str) -> list[ImageDelivery]:
        return self._query("SELECT * FROM image_deliveries WHERE batch_id=? ORDER BY rowid", [batch_id])

    def list(self, *, session_id: str | None = None, sha256: str | None = None, reference_id: str | None = None,
             limit: int = 500) -> list[ImageDelivery]:
        q, args = "SELECT * FROM image_deliveries WHERE 1=1", []
        for col, value in (("session_id", session_id), ("sha256", sha256), ("ref_id", reference_id)):
            if value:
                q += f" AND {col}=?"
                args.append(value)
        q += " ORDER BY recorded_ts DESC, rowid DESC LIMIT ?"
        args.append(limit)
        return self._query(q, args)

    def _query(self, q: str, args: list) -> list[ImageDelivery]:
        if self.closed:
            return []
        with self._lock:
            return [ImageDelivery(**{k: r[k] for k in r.keys()}) for r in self._db.execute(q, args)]

    def _changed(self, rows: list[ImageDelivery]) -> None:
        if self.on_change is not None and rows:
            try:
                self.on_change(rows)
            except Exception:                                                 # noqa: BLE001 - exibir nao desfaz o registro
                log.exception("on_change do registro de imagem falhou")

    def close(self) -> None:
        with self._lock:
            if not self.closed:
                self.closed = True
                self._db.close()


def send_with_images(adapter: ProviderAdapter, ledger: ImageLedger | None, session_id: str, text: str, on_event,
                     images: Iterable[ImagePayload | str | Path], *, provider_id: str, model_id: str,
                     requested_by: str, purpose: str, studio_session_id: str = "",
                     reference: ReferenceLink | None = None, roles: Sequence[str] | None = None) -> cf.Future:
    """Unica porta do Studio para mandar imagem a um agente: valida, registra, e so entao envia os mesmos bytes.

    SENT quer dizer "o adaptador aceitou o pedido com estas imagens", nao "o modelo recebeu": a confirmacao do provedor
    (contagem de tokens de entrada) e de quem le o uso do turno."""
    if ledger is None:
        raise ImageLedgerError("sem registro de imagem configurado: nada enviado")
    payloads = load_images(images)                                            # ImageRejected antes de gravar
    rows = ledger.record_delivery(payloads, provider_id=provider_id, model_id=model_id,
                                  session_id=studio_session_id or session_id, provider_session_id=session_id,
                                  requested_by=requested_by, purpose=purpose, reference=reference, roles=roles)
    batch = rows[0].batch_id
    try:
        fut = adapter.send_prompt(session_id, text, on_event, images=payloads)
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        try:
            ledger.mark(batch, "NOT_SENT", f"{code}: {exc}")
        except ImageLedgerError:
            log.exception("nao foi possivel marcar NOT_SENT no lote %s", batch)
        raise
    try:
        ledger.mark(batch, "SENT")
    except ImageLedgerError:                                                  # ja foi; o lote fica RECORDED (honesto)
        log.exception("imagem enviada, mas o lote %s ficou RECORDED", batch)
    return fut
