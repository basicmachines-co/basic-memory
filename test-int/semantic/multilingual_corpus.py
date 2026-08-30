"""Versioned multilingual retrieval corpus for embedding model evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from basic_memory import db
from basic_memory.models import Entity
from basic_memory.services.search_service import SearchService


class RetrievalCaseKind(StrEnum):
    """The retrieval behavior exercised by one benchmark query."""

    ENGLISH_BASELINE = "english-baseline"
    SAME_LANGUAGE = "same-language"
    CROSS_LANGUAGE = "cross-language"
    MIXED_LANGUAGE = "mixed-language"
    CHUNK_BOUNDARY = "chunk-boundary"
    NEGATIVE = "negative"


@dataclass(frozen=True, slots=True)
class MultilingualDocument:
    """A benchmark note with an explicit content language."""

    title: str
    permalink: str
    language: str
    content: str


@dataclass(frozen=True, slots=True)
class MultilingualQuery:
    """A query with exact relevance judgments."""

    name: str
    text: str
    language: str
    kind: RetrievalCaseKind
    relevant_permalinks: tuple[str, ...]
    required_chunk_text: str | None = None


@dataclass(frozen=True, slots=True)
class MultilingualCorpus:
    """A validated, versioned set of documents and retrieval judgments."""

    version: str
    documents: tuple[MultilingualDocument, ...]
    queries: tuple[MultilingualQuery, ...]

    def __post_init__(self) -> None:
        document_permalinks = [document.permalink for document in self.documents]
        if len(document_permalinks) != len(set(document_permalinks)):
            raise ValueError("Multilingual corpus document permalinks must be unique")

        query_names = [query.name for query in self.queries]
        if len(query_names) != len(set(query_names)):
            raise ValueError("Multilingual corpus query names must be unique")

        known_permalinks = set(document_permalinks)
        for query in self.queries:
            if query.kind is RetrievalCaseKind.NEGATIVE:
                if query.relevant_permalinks:
                    raise ValueError(f"Negative query {query.name!r} cannot have relevant notes")
                continue
            if not query.relevant_permalinks:
                raise ValueError(f"Positive query {query.name!r} needs a relevance judgment")
            if query.kind is RetrievalCaseKind.CHUNK_BOUNDARY and not query.required_chunk_text:
                raise ValueError(f"Chunk-boundary query {query.name!r} needs required chunk text")
            unknown = set(query.relevant_permalinks) - known_permalinks
            if unknown:
                raise ValueError(
                    f"Query {query.name!r} references unknown permalinks: {sorted(unknown)!r}"
                )


DOCUMENTS = (
    MultilingualDocument(
        title="Choose Columns for a Composite Index",
        permalink="multilingual/en-composite-index",
        language="en",
        content=(
            "For queries that filter by tenant and then sort by creation time, create a "
            "composite database index with tenant_id first and created_at second. Confirm "
            "that the query planner uses the index instead of scanning every row."
        ),
    ),
    MultilingualDocument(
        title="在国外补办遗失的护照",
        permalink="multilingual/zh-emergency-passport",
        language="zh",
        content=(
            "在国外遗失护照后，应先向当地警方报案，然后联系本国大使馆或领事馆。"
            "准备身份证明、证件照片和行程信息，以便申请紧急旅行证件并按时回国。"
        ),
    ),
    MultilingualDocument(
        title="Report Delayed Checked Baggage",
        permalink="multilingual/en-delayed-baggage",
        language="en",
        content=(
            "Report delayed checked baggage before leaving the airport. Keep the baggage claim "
            "number and receipts for reasonable replacement toiletries and clothing while the "
            "airline traces the suitcase."
        ),
    ),
    MultilingualDocument(
        title="監視で見逃したファイルを再照合する",
        permalink="multilingual/ja-watcher-reconciliation",
        language="ja",
        content=(
            "ファイル監視が停止して変更イベントを取りこぼした場合は、整合性スキャンを実行します。"
            "保存済みのチェックサムと現在のファイル内容を比較し、差分があるノートだけを再索引します。"
        ),
    ),
    MultilingualDocument(
        title="Merge Divergent Note Edits",
        permalink="multilingual/en-sync-conflict",
        language="en",
        content=(
            "When local and remote writers edit the same note from a shared base, preserve both "
            "versions. Compare the conflict copy and merge the intended paragraphs manually "
            "instead of silently choosing one writer."
        ),
    ),
    MultilingualDocument(
        title="인증 앱 없이 계정 접근 복구하기",
        permalink="multilingual/ko-authenticator-recovery",
        language="ko",
        content=(
            "인증 앱이 들어 있는 휴대전화를 잃어버렸다면 먼저 일회용 복구 코드를 사용합니다. "
            "복구 코드도 없으면 고객 지원에서 신원을 확인한 뒤 이중 인증을 재설정해야 합니다."
        ),
    ),
    MultilingualDocument(
        title="Reset a Forgotten Password",
        permalink="multilingual/en-password-reset",
        language="en",
        content=(
            "Request a password reset email from the sign-in page. Completing the link creates a "
            "new password and invalidates old sessions, but it does not remove two-factor "
            "authentication from the account."
        ),
    ),
    MultilingualDocument(
        title="استعادة الصفوف المحذوفة عن طريق الخطأ",
        permalink="multilingual/ar-point-in-time-recovery",
        language="ar",
        content=(
            "لاستعادة صفوف حُذفت عن طريق الخطأ، استعد آخر نسخة احتياطية كاملة في قاعدة بيانات معزولة، "
            "ثم أعد تشغيل سجل الكتابة المسبقة حتى اللحظة التي سبقت الحذف مباشرة."
        ),
    ),
    MultilingualDocument(
        title="Roll Back a Failed Schema Migration",
        permalink="multilingual/en-migration-rollback",
        language="en",
        content=(
            "If a schema migration fails during deployment, stop application writes and run the "
            "tested downgrade revision. A migration rollback restores the previous schema but "
            "does not recover rows that were already deleted."
        ),
    ),
    MultilingualDocument(
        title="Получить общие заметки без удаления локальных файлов",
        permalink="multilingual/ru-additive-pull",
        language="ru",
        content=(
            "Чтобы скачать новые общие заметки и сохранить файлы, существующие только на ноутбуке, "
            "используйте добавочное облачное получение. Зеркальная синхронизация для этого не подходит, "
            "потому что она может удалить локальные файлы, отсутствующие в источнике."
        ),
    ),
    MultilingualDocument(
        title="Understand Mirror Synchronization",
        permalink="multilingual/en-mirror-sync",
        language="en",
        content=(
            "A one-way mirror makes the destination match the source exactly. Files absent from "
            "the source may therefore be deleted at the destination; use an additive transfer "
            "when destination-only files must survive."
        ),
    ),
    MultilingualDocument(
        title="Reactivar una masa madre débil",
        permalink="multilingual/es-sourdough-starter",
        language="es",
        content=(
            "Una masa madre que apenas sube necesita alimentaciones regulares y una temperatura "
            "templada. Conserva una porción pequeña, añade el mismo peso de harina y agua, y espera "
            "a que duplique su volumen antes de preparar el pan."
        ),
    ),
    MultilingualDocument(
        title="Fix Soup That Is Too Salty",
        permalink="multilingual/en-salty-soup",
        language="en",
        content=(
            "Dilute an over-salted soup with unsalted stock or water, then add vegetables or grains "
            "to spread the seasoning across more food. A raw potato does not selectively absorb "
            "only the excess salt."
        ),
    ),
    MultilingualDocument(
        title="เตรียมชุดฉุกเฉินสำหรับน้ำท่วม",
        permalink="multilingual/th-flood-emergency-kit",
        language="th",
        content=(
            "ชุดฉุกเฉินสำหรับน้ำท่วมควรมีน้ำดื่ม ยาประจำตัว วิทยุใช้แบตเตอรี่ ไฟฉาย และสำเนาเอกสารสำคัญ"
            "ที่เก็บในถุงกันน้ำ วางชุดไว้ในจุดที่หยิบได้ทันทีเมื่อต้องอพยพขึ้นที่สูง"
        ),
    ),
    MultilingualDocument(
        title="Prepare an Earthquake Bedside Kit",
        permalink="multilingual/en-earthquake-kit",
        language="en",
        content=(
            "Keep sturdy shoes, gloves, a flashlight, and drinking water beside the bed for an "
            "earthquake. The shoes protect against broken glass when leaving a damaged building "
            "after shaking stops."
        ),
    ),
    MultilingualDocument(
        title="Deployment Rollback Runbook",
        permalink="multilingual/mixed-deployment-runbook",
        language="mixed",
        content=(
            "Production rollback 手順: first freeze new writes and record the release revision. "
            "次に health check の失敗を確認し、直前の安定版イメージへ戻します。After rollback, "
            "verify database compatibility, queue depth, and the customer-facing search path."
        ),
    ),
    MultilingualDocument(
        title="보관 기간 정책의 법적 예외",
        permalink="multilingual/ko-long-retention-exception",
        language="ko",
        content=(
            "# 보관 정책 검토\n\n"
            "일반 프로젝트의 활동 기록은 분기마다 검토합니다. 오래된 임시 내보내기 파일은 "
            "저장 비용을 줄이기 위해 정리하고, 소유자가 없는 시험 데이터도 같은 절차를 따릅니다.\n\n"
            "## 운영 절차\n\n"
            "담당자는 삭제 후보 목록을 만들고 프로젝트 소유자에게 확인을 요청합니다. 확인 기간에는 "
            "파일을 변경하지 않으며, 승인 결과와 실행 시간을 감사 기록에 남깁니다. 복구 연습은 별도의 "
            "복사본에서 수행하고 원본 데이터의 체크섬과 비교합니다.\n\n"
            "## 보존 대상 분류\n\n"
            "운영 데이터, 고객 지원 기록, 결제 증빙, 보안 사건 자료는 서로 다른 보존 근거를 가집니다. "
            "담당자는 각 자료의 소유 조직과 생성 시점, 계약상 의무, 적용 지역을 확인한 뒤 분류표에 "
            "근거를 기록합니다. 분류가 불명확한 자료는 임의로 삭제하지 않고 개인정보 보호 담당자와 "
            "서비스 책임자에게 검토를 요청합니다. 동일한 사건에서 생성된 첨부 파일과 내보내기 사본도 "
            "원본 기록과 같은 분류를 유지해야 합니다.\n\n"
            "## 삭제 전 검증\n\n"
            "자동 정리 작업은 실행 전에 예상 파일 수와 총 크기, 가장 오래된 생성일, 관련 프로젝트를 "
            "미리 보고합니다. 운영자는 샘플을 열어 분류가 맞는지 확인하고 최근 복구 훈련의 결과가 "
            "유효한지도 검토합니다. 승인된 목록은 변경할 수 없는 감사 이벤트로 저장하며, 실제 삭제 "
            "직전에 새 보존 요청이나 진행 중인 조사 표식이 생기지 않았는지 다시 확인합니다. 검증 중 "
            "하나라도 불일치가 발견되면 전체 작업을 중단하고 새 목록을 생성합니다.\n\n"
            "## 예외\n\n"
            "법적 보존 명령이 적용된 고객 기록은 일반 만료 일정을 무시해야 합니다. 사건 담당자가 "
            "보존 해제를 서면으로 승인할 때까지 해당 기록과 관련 감사 로그를 삭제하거나 압축 저장소로 "
            "이동해서는 안 됩니다. 이 예외는 자동 정리 작업보다 항상 우선합니다."
        ),
    ),
)


QUERIES = (
    MultilingualQuery(
        "en-composite-index",
        "Which column should lead an index for tenant-filtered rows ordered by creation time?",
        "en",
        RetrievalCaseKind.ENGLISH_BASELINE,
        ("multilingual/en-composite-index",),
    ),
    MultilingualQuery(
        "zh-passport-same-language",
        "在海外把护照弄丢了，怎样联系领事机构办理临时回国证件？",
        "zh",
        RetrievalCaseKind.SAME_LANGUAGE,
        ("multilingual/zh-emergency-passport",),
    ),
    MultilingualQuery(
        "ja-watcher-same-language",
        "監視サービスの停止中に見逃した更新をチェックサムで見つけるにはどうしますか？",
        "ja",
        RetrievalCaseKind.SAME_LANGUAGE,
        ("multilingual/ja-watcher-reconciliation",),
    ),
    MultilingualQuery(
        "ko-authenticator-same-language",
        "휴대전화와 인증 앱을 잃어버렸고 복구 코드도 없을 때 계정에 다시 들어가는 방법은?",
        "ko",
        RetrievalCaseKind.SAME_LANGUAGE,
        ("multilingual/ko-authenticator-recovery",),
    ),
    MultilingualQuery(
        "ar-database-recovery-same-language",
        "كيف أسترجع سجلات حُذفت بالخطأ إلى الوقت السابق للحذف مباشرة؟",
        "ar",
        RetrievalCaseKind.SAME_LANGUAGE,
        ("multilingual/ar-point-in-time-recovery",),
    ),
    MultilingualQuery(
        "ru-additive-pull-same-language",
        "Как получить общие заметки из облака и не удалить файлы, которые есть только локально?",
        "ru",
        RetrievalCaseKind.SAME_LANGUAGE,
        ("multilingual/ru-additive-pull",),
    ),
    MultilingualQuery(
        "es-sourdough-same-language",
        "¿Cómo recupero una masa madre débil y qué señal indica que ya está lista para hornear?",
        "es",
        RetrievalCaseKind.SAME_LANGUAGE,
        ("multilingual/es-sourdough-starter",),
    ),
    MultilingualQuery(
        "th-flood-kit-same-language",
        "ถ้าต้องอพยพหนีน้ำท่วม ควรเตรียมยา เอกสาร และอุปกรณ์อะไรไว้ในถุงกันน้ำ?",
        "th",
        RetrievalCaseKind.SAME_LANGUAGE,
        ("multilingual/th-flood-emergency-kit",),
    ),
    MultilingualQuery(
        "zh-passport-cross-language",
        "I lost my passport overseas. How do I obtain an emergency travel document?",
        "en",
        RetrievalCaseKind.CROSS_LANGUAGE,
        ("multilingual/zh-emergency-passport",),
    ),
    MultilingualQuery(
        "ja-watcher-cross-language",
        "How can a checksum scan find file changes missed while the watcher was offline?",
        "en",
        RetrievalCaseKind.CROSS_LANGUAGE,
        ("multilingual/ja-watcher-reconciliation",),
    ),
    MultilingualQuery(
        "ko-authenticator-cross-language",
        "What recovery path is available after losing the phone that held my authenticator?",
        "en",
        RetrievalCaseKind.CROSS_LANGUAGE,
        ("multilingual/ko-authenticator-recovery",),
    ),
    MultilingualQuery(
        "ar-database-recovery-cross-language",
        "How do I restore deleted database rows to the instant before the deletion?",
        "en",
        RetrievalCaseKind.CROSS_LANGUAGE,
        ("multilingual/ar-point-in-time-recovery",),
    ),
    MultilingualQuery(
        "ru-additive-pull-cross-language",
        "Download shared cloud notes without removing files that exist only on my laptop.",
        "en",
        RetrievalCaseKind.CROSS_LANGUAGE,
        ("multilingual/ru-additive-pull",),
    ),
    MultilingualQuery(
        "es-sourdough-cross-language",
        "My sourdough culture barely rises. How should I feed it until it doubles?",
        "en",
        RetrievalCaseKind.CROSS_LANGUAGE,
        ("multilingual/es-sourdough-starter",),
    ),
    MultilingualQuery(
        "th-flood-kit-cross-language",
        "What medicine, documents, and radio should be packed for a flood evacuation?",
        "en",
        RetrievalCaseKind.CROSS_LANGUAGE,
        ("multilingual/th-flood-emergency-kit",),
    ),
    MultilingualQuery(
        "mixed-runbook-japanese",
        "リリース後の health check が失敗した場合、どの rollback 手順を確認しますか？",
        "mixed",
        RetrievalCaseKind.MIXED_LANGUAGE,
        ("multilingual/mixed-deployment-runbook",),
    ),
    MultilingualQuery(
        "mixed-runbook-english",
        "Which runbook freezes writes, restores the prior image, and verifies queue depth?",
        "en",
        RetrievalCaseKind.MIXED_LANGUAGE,
        ("multilingual/mixed-deployment-runbook",),
    ),
    MultilingualQuery(
        "long-retention-korean",
        "법적 보존 명령이 있는 고객 기록은 자동 만료 작업에서 어떻게 처리해야 합니까?",
        "ko",
        RetrievalCaseKind.CHUNK_BOUNDARY,
        ("multilingual/ko-long-retention-exception",),
        "법적 보존 명령",
    ),
    MultilingualQuery(
        "long-retention-cross-language",
        "What exception prevents automatic deletion while a legal hold remains active?",
        "en",
        RetrievalCaseKind.CHUNK_BOUNDARY,
        ("multilingual/ko-long-retention-exception",),
        "법적 보존 명령",
    ),
    MultilingualQuery(
        "negative-tomato-blight",
        "How should I treat fungal blight on tomato plants?",
        "en",
        RetrievalCaseKind.NEGATIVE,
        (),
    ),
    MultilingualQuery(
        "negative-zh-violin",
        "初学者应该怎样给小提琴调音？",
        "zh",
        RetrievalCaseKind.NEGATIVE,
        (),
    ),
    MultilingualQuery(
        "negative-ar-solar-panels",
        "كيف أحسب زاوية تركيب الألواح الشمسية على السطح؟",
        "ar",
        RetrievalCaseKind.NEGATIVE,
        (),
    ),
    MultilingualQuery(
        "negative-th-coffee",
        "ควรบดเมล็ดกาแฟละเอียดแค่ไหนสำหรับเครื่องเอสเปรสโซ?",
        "th",
        RetrievalCaseKind.NEGATIVE,
        (),
    ),
)


MULTILINGUAL_CORPUS = MultilingualCorpus(
    version="multilingual-retrieval-v1",
    documents=DOCUMENTS,
    queries=QUERIES,
)


async def seed_multilingual_documents(
    search_service: SearchService,
    documents: Sequence[MultilingualDocument],
) -> list[Entity]:
    """Index the corpus through the production search and vector-sync path."""
    entities: list[Entity] = []
    for document in documents:
        async with db.scoped_session(search_service.session_maker) as session:
            entity = await search_service.entity_repository.create(
                session,
                {
                    "title": document.title,
                    "note_type": "benchmark",
                    "entity_metadata": {
                        "tags": ["benchmark", "multilingual", document.language],
                        "corpus": MULTILINGUAL_CORPUS.version,
                    },
                    "content_type": "text/markdown",
                    "permalink": document.permalink,
                    "file_path": f"{document.permalink}.md",
                },
            )
        await search_service.index_entity_data(entity, content=document.content)
        await search_service.sync_entity_vectors(entity.id)
        entities.append(entity)

    return entities
