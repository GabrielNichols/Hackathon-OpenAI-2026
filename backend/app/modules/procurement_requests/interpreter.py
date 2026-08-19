"""Small replaceable Portuguese interpreter used by the local prototype.

It is intentionally conservative: only facts backed by a message span are
returned, and candidates which contradict the current request are surfaced as
conflicts instead of overwriting the stored value.  A model-backed adapter can
replace this class through ``ProcurementInterpretationPort`` later.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from .ports import Clock, ProcurementInterpretationPort
from .schemas import (
    MAX_BUYER_MESSAGE_CHARS,
    ProcurementInterpretationResult,
    ProcurementPolicySnapshot,
    ProcurementRequestPatch,
    RequestLike,
)
from .service import ProcurementRequestService

_WEEKDAYS = {
    "segunda": 0,
    "terca": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sabado": 5,
    "domingo": 6,
}
_KNOWN_DISTRICTS = (
    "Vila Olímpia",
    "Vila Olimpia",
    "Pinheiros",
    "Itaim Bibi",
    "Moema",
    "Brooklin",
    "Berrini",
    "Paulista",
    "Jardins",
    "Morumbi",
    "Santo Amaro",
    "Centro",
)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class LocalPortugueseProcurementInterpreter(ProcurementInterpretationPort):
    """Evidence-first parser for the hackathon's corporate catering scenario."""

    def __init__(
        self,
        clock: Clock,
        *,
        service: ProcurementRequestService | None = None,
        timezone: str = "America/Sao_Paulo",
        default_policy: ProcurementPolicySnapshot | None = None,
    ) -> None:
        self._clock = clock
        self._timezone = ZoneInfo(timezone)
        self._policy = default_policy or ProcurementPolicySnapshot()
        self._service = service or ProcurementRequestService(
            clock=clock,
            timezone=timezone,
            default_policy=self._policy,
        )

    async def interpret(
        self,
        message: str,
        current_request: RequestLike | None = None,
        policy: ProcurementPolicySnapshot | None = None,
    ) -> ProcurementInterpretationResult:
        message = message.strip()
        if not message:
            raise ValueError("message cannot be empty")
        if len(message) > MAX_BUYER_MESSAGE_CHARS:
            raise ValueError(f"message cannot exceed {MAX_BUYER_MESSAGE_CHARS} characters")

        now = self._aware_now().astimezone(self._timezone)
        values: dict[str, Any] = {}
        evidence: dict[str, str] = {}
        confidence: dict[str, float] = {}
        ambiguities: list[str] = []
        assumptions: list[str] = []

        def add(field: str, value: Any, source: str, score: float = 0.98) -> None:
            if field not in values:
                values[field] = value
                evidence[field] = source.strip()
                confidence[field] = score

        if current_request is None or current_request.description is None:
            add("description", message, message, 1.0)

        category_match = re.search(
            r"\b(coffee[ -]?break|caf[eé] da manh[ãa]|almo[cç]o|"
            r"refei[cç][aã]o|buffet|catering|lanche(?:s)?)\b",
            message,
            re.IGNORECASE,
        )
        if category_match:
            add("category", "corporate_catering", category_match.group(0), 0.99)

        deadline, deadline_evidence, deadline_span, deadline_ambiguity = (
            self._extract_response_deadline(message, now)
        )
        if deadline is not None:
            add("response_deadline", deadline, deadline_evidence, 0.97)
        if deadline_ambiguity:
            ambiguities.append(deadline_ambiguity)

        event_text = message
        if deadline_span is not None:
            start, end = deadline_span
            event_text = message[:start] + " " * (end - start) + message[end:]
        event_date, date_evidence = self._extract_date(event_text, now.date())
        if event_date is not None:
            add("event_date", event_date, date_evidence, 0.96)
        elif re.search(
            r"\b(final do m[eê]s|daqui a alguns dias|semana que vem)\b",
            event_text,
            re.IGNORECASE,
        ):
            ambiguities.append("AMBIGUOUS_EVENT_DATE")

        delivery_time, delivery_evidence = self._extract_delivery_time(
            event_text,
        )
        if delivery_time is not None:
            add("delivery_time", delivery_time, delivery_evidence, 0.97)

        people_match = re.search(
            r"\b(?:para\s+)?(\d{1,5})\s*"
            r"(?:pessoas?|convidad[oa]s?|participantes?|colaboradores?)\b",
            message,
            re.IGNORECASE,
        )
        if people_match:
            add("people_count", int(people_match.group(1)), people_match.group(0))

        money = self._extract_budget(message)
        if money is not None:
            cents, source = money
            add("maximum_total_cents", cents, source, 0.99)
            add("currency", "BRL", source, 1.0)

        dietary_patterns = {
            "vegetarian_count": (r"\b(\d{1,5})\s*(?:pessoas?\s+)?vegetarian[oa]s?\b"),
            "vegan_count": r"\b(\d{1,5})\s*(?:pessoas?\s+)?vegan[oa]s?\b",
            "gluten_free_count": (
                r"\b(\d{1,5})\s*(?:pessoas?\s+)?(?:com\s+)?"
                r"(?:restri[cç][aã]o\s+(?:a|ao)\s+gl[uú]ten|sem\s+gl[uú]ten|cel[ií]ac[oa]s?)\b"
            ),
        }
        for field, pattern in dietary_patterns.items():
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                add(field, int(match.group(1)), match.group(0), 0.99)

        ambiguous_invoice = re.search(
            r"\b(?:fornecedor|buffet|empresa)?\s*n[aã]o\s+pode\s+emitir\s+"
            r"(?:nota\s+fiscal|nf)\b",
            message,
            re.IGNORECASE,
        )
        if ambiguous_invoice:
            ambiguities.append("AMBIGUOUS_INVOICE_REQUIREMENT")
        else:
            invoice = self._extract_invoice_requirement(message)
            if invoice is not None:
                add("invoice_required", invoice[0], invoice[1], 0.98)

        plastic = self._extract_plastic_requirement(message)
        if plastic is not None:
            add("no_single_use_plastic", plastic[0], plastic[1], 0.96)

        full_address = re.search(
            r"\b((?:Rua|R\.|Avenida|Av\.|Alameda|Pra[cç]a)\s+"
            r"[^,.;\n]{3,100}(?:,\s*\d+[A-Za-z]?)?)",
            message,
            re.IGNORECASE,
        )
        if full_address:
            add("full_address", full_address.group(1).strip(), full_address.group(0), 0.98)

        district = self._extract_district(message)
        if district is not None:
            add("location_district", district[0], district[1], 0.94)

        city_match = re.search(r"\bS[aã]o Paulo\b", message, re.IGNORECASE)
        if city_match:
            add("location_city", "São Paulo", city_match.group(0), 0.99)

        quote_count = re.search(
            r"\b(?:consultar|cotar\s+com|pedir\s+(?:para|a)|quero)\s+"
            r"(\d{1,2})\s*(?:fornecedores?|cota[cç][oõ]es?)\b",
            message,
            re.IGNORECASE,
        )
        if quote_count:
            add("desired_quote_count", int(quote_count.group(1)), quote_count.group(0), 0.94)

        approver = self._extract_approver(message)
        if approver is not None:
            add("approver_user_id", approver[0], approver[1], 0.92)

        candidate_patch = ProcurementRequestPatch.model_validate(values)
        conflicts = []
        if current_request is not None:
            conflicts = self._service.detect_conflicts(
                current_request,
                candidate_patch,
                evidence,
            )
            conflict_fields = {conflict.field for conflict in conflicts}
            if conflict_fields:
                for field in sorted(conflict_fields):
                    ambiguities.append(f"CONFLICTING_{field.upper()}")
                safe_values = {
                    field: getattr(candidate_patch, field)
                    for field in candidate_patch.model_fields_set
                    if field not in conflict_fields
                }
                candidate_patch = ProcurementRequestPatch.model_validate(safe_values)

        merged_values: dict[str, Any] = {}
        if current_request is not None:
            merged_values.update(
                {
                    field: getattr(current_request, field)
                    for field in ProcurementRequestPatch.model_fields
                }
            )
        merged_values.update(
            {field: getattr(candidate_patch, field) for field in candidate_patch.model_fields_set}
        )
        virtual_request = ProcurementRequestPatch.model_validate(merged_values)
        effective_policy = policy or self._policy
        missing = self._service.missing_required_fields(virtual_request, effective_policy)
        for issue in self._service.blocking_issues(virtual_request, effective_policy):
            if issue not in ambiguities:
                ambiguities.append(issue)

        return ProcurementInterpretationResult(
            extracted_fields=candidate_patch,
            evidence=evidence,
            ambiguities=ambiguities,
            assumptions=assumptions,
            conflicts=conflicts,
            missing_required_fields=missing,
            confidence_by_field=confidence,
        )

    def interpret_sync(
        self,
        message: str,
        current_request: RequestLike | None = None,
        policy: ProcurementPolicySnapshot | None = None,
    ) -> ProcurementInterpretationResult:
        """Synchronous counterpart for adapters which do not run an event loop."""

        # Keep one implementation of the parser while offering a convenient
        # local API. This method intentionally duplicates no async side effect.
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.interpret(message, current_request, policy))
        raise RuntimeError("interpret_sync cannot be used inside a running event loop")

    def _aware_now(self) -> datetime:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock.now() must return a timezone-aware datetime")
        return now

    def _extract_response_deadline(
        self,
        message: str,
        now: datetime,
    ) -> tuple[datetime | None, str, tuple[int, int] | None, str | None]:
        anchor = None
        for pattern in (
            r"\b(?:respostas?|propostas?|retorno)\s+(?:at[eé])\b[^.;\n]{0,60}",
            r"\bprazo(?:\s+m[aá]ximo)?(?:\s+de\s+resposta)?\b[^.;\n]{0,60}",
            r"\bcota[cç][oõ]es?\s+(?:at[eé])\b[^.;\n]{0,60}",
        ):
            anchor = re.search(pattern, message, re.IGNORECASE)
            if anchor:
                break
        if not anchor:
            return None, "", None, None
        clause = anchor.group(0)
        deadline_date, _ = self._extract_date(clause, now.date())
        deadline_time, _ = self._extract_any_time(clause)
        if deadline_date is None:
            return None, clause, anchor.span(), "AMBIGUOUS_RESPONSE_DEADLINE"
        if deadline_time is None:
            return None, clause, anchor.span(), "RESPONSE_DEADLINE_TIME_REQUIRED"
        return (
            datetime.combine(deadline_date, deadline_time, tzinfo=self._timezone),
            clause,
            anchor.span(),
            None,
        )

    def _extract_date(
        self,
        text: str,
        today: date,
    ) -> tuple[date | None, str]:
        iso_match = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
        if iso_match:
            try:
                return (
                    date(*map(int, iso_match.groups())),
                    iso_match.group(0),
                )
            except ValueError:
                return None, iso_match.group(0)

        br_match = re.search(
            r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b",
            text,
        )
        if br_match:
            day, month = int(br_match.group(1)), int(br_match.group(2))
            year_text = br_match.group(3)
            year = today.year if year_text is None else int(year_text)
            if year < 100:
                year += 2000
            try:
                candidate = date(year, month, day)
                if year_text is None and candidate < today:
                    candidate = date(year + 1, month, day)
                return candidate, br_match.group(0)
            except ValueError:
                return None, br_match.group(0)

        normalized = _normalize(text)
        relative_patterns = (
            (r"\bdepois de amanha\b", 2),
            (r"\bamanha\b", 1),
            (r"\bhoje\b", 0),
        )
        for pattern, days in relative_patterns:
            match = re.search(pattern, normalized)
            if match:
                return today + timedelta(days=days), text[match.start() : match.end()]

        weekday_match = re.search(
            r"\b(?:(?:proxim[ao]|nesta|essa)\s+)?"
            r"(segunda|terca|quarta|quinta|sexta|sabado|domingo)(?:-feira)?\b",
            normalized,
        )
        if weekday_match:
            target = _WEEKDAYS[weekday_match.group(1)]
            delta = (target - today.weekday()) % 7
            if delta == 0:
                delta = 7
            return today + timedelta(days=delta), text[weekday_match.start() : weekday_match.end()]
        return None, ""

    def _extract_delivery_time(self, text: str) -> tuple[time | None, str]:
        contextual = re.search(
            r"\b(?:entreg(?:a|ar|ue)|evento|in[ií]cio|come[cç]a)\b"
            r"[^.;\n]{0,35}?\b(?:[aà]s?\s*)?"
            r"(\d{1,2})(?:h|:)(\d{2})?\b",
            text,
            re.IGNORECASE,
        )
        if contextual:
            parsed = _make_time(contextual.group(1), contextual.group(2))
            return parsed, contextual.group(0)
        matches = list(
            re.finditer(
                r"\b(?:[aà]s?\s*)?(\d{1,2})(?:h|:)(\d{2})?\b",
                text,
                re.IGNORECASE,
            )
        )
        if len(matches) == 1:
            parsed = _make_time(matches[0].group(1), matches[0].group(2))
            return parsed, matches[0].group(0)
        return None, ""

    def _extract_any_time(self, text: str) -> tuple[time | None, str]:
        match = re.search(
            r"\b(?:[aà]s?\s*)?(\d{1,2})(?:h|:)(\d{2})?\b",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None, ""
        return _make_time(match.group(1), match.group(2)), match.group(0)

    def _extract_budget(self, message: str) -> tuple[int, str] | None:
        patterns = (
            r"\b(?:or[cç]amento(?:\s+m[aá]ximo)?(?:\s+total)?|limite(?:\s+m[aá]ximo)?|"
            r"teto|valor\s+m[aá]ximo)\s*(?:de|é|:)?\s*R\$?\s*"
            r"(\d[\d.,]*)(\s*mil)?",
            r"\b(?:or[cç]amento(?:\s+m[aá]ximo)?(?:\s+total)?|limite(?:\s+m[aá]ximo)?|"
            r"teto|valor\s+m[aá]ximo)\s*(?:de|é|:)?\s*"
            r"(\d[\d.,]*)(\s*mil)?\s*reais\b",
        )
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if not match:
                continue
            # ``[\d.,]*`` intentionally accepts Brazilian grouping and decimal
            # separators, but must not consume sentence punctuation.
            raw = match.group(1).rstrip(".,")
            is_thousands = bool(match.group(2))
            try:
                amount = _parse_brl_decimal(raw, is_thousands=is_thousands)
            except InvalidOperation:
                return None
            cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            if cents > 0:
                return cents, match.group(0)
        return None

    def _extract_invoice_requirement(self, message: str) -> tuple[bool, str] | None:
        negative = re.search(
            r"(?:\b(?:n[aã]o\s+(?:precisa|necessita|exige)|sem\s+necessidade\s+de)\b"
            r"[^.;\n]{0,30}\b(?:nota\s+fiscal|nf)\b|"
            r"\b(?:nota\s+fiscal|nf)\b[^.;\n]{0,20}"
            r"\bn[aã]o\s+é\s+obrigat[oó]ri[oa]\b)",
            message,
            re.IGNORECASE,
        )
        if negative:
            return False, negative.group(0)
        positive = re.search(
            r"(?:\b(?:obrigat[oó]ri[oa]|precisa|exige|emitir|emiss[aã]o|com)\b"
            r"[^.;\n]{0,35}\b(?:nota\s+fiscal|nf)\b|"
            r"\b(?:nota\s+fiscal|nf)\b[^.;\n]{0,35}"
            r"\b(?:obrigat[oó]ri[oa]|precisa|exige|emitir)\b)",
            message,
            re.IGNORECASE,
        )
        if positive:
            return True, positive.group(0)
        return None

    def _extract_plastic_requirement(self, message: str) -> tuple[bool, str] | None:
        allowed = re.search(
            r"(?:\bsem\s+(?:qualquer\s+)?restri[cç][aã]o\s+"
            r"(?:a|ao|de)?\s*pl[aá]stic|"
            r"\bn[aã]o\s+(?:precisa|é\s+necess[aá]rio)\s+evitar\b"
            r"[^.;\n]{0,35}\bpl[aá]stic|\bpl[aá]stic[^.;\n]{0,25}"
            r"\b(?:permitido|pode\s+usar)\b)",
            message,
            re.IGNORECASE,
        )
        if allowed:
            return False, allowed.group(0)
        forbidden = re.search(
            r"(?:\b(?:evitar|sem|proibir|n[aã]o\s+usar|livre\s+de)\b"
            r"[^.;\n]{0,45}\b(?:descart[aá]ve(?:l|is)\s+)?pl[aá]stic|"
            r"\bn[aã]o\s+pode\s+(?:ter|usar|conter)\b[^.;\n]{0,35}"
            r"\bpl[aá]stic|"
            r"\bpl[aá]stic[^.;\n]{0,35}\b(?:n[aã]o|proibid|evitar)\b)",
            message,
            re.IGNORECASE,
        )
        if forbidden:
            return True, forbidden.group(0)
        return None

    def _extract_district(self, message: str) -> tuple[str, str] | None:
        explicit = re.search(
            r"\bbairro\s+(?:de|da|do)?\s*([A-ZÀ-Ý][A-Za-zÀ-ÿ' -]{1,60})",
            message,
        )
        if explicit:
            value = re.split(r"\s+(?:e|com|para|às|as)\s+", explicit.group(1))[0]
            return value.strip(" ,.;"), explicit.group(0)
        alternatives = "|".join(re.escape(item) for item in _KNOWN_DISTRICTS)
        known = re.search(
            rf"\b(?:na|no|em)\s+({alternatives})\b",
            message,
            re.IGNORECASE,
        )
        if known:
            raw = known.group(1)
            canonical = next(
                (item for item in _KNOWN_DISTRICTS if _normalize(item) == _normalize(raw)),
                raw,
            )
            if canonical == "Vila Olimpia":
                canonical = "Vila Olímpia"
            return canonical, known.group(0)
        return None

    def _extract_approver(self, message: str) -> tuple[str, str] | None:
        match = re.search(
            r"\b(?:aprovador(?:a)?|respons[aá]vel\s+pela\s+aprova[cç][aã]o)\b"
            r"\s*(?:é|ser[aá]|:|=|vai\s+ser)?\s*"
            r"([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9_.@ -]{0,80})",
            message,
            re.IGNORECASE,
        )
        if not match:
            return None
        value = re.split(
            r"\s+(?:e\s+o|e\s+a|com|at[eé]|para)\s+",
            match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" ,.;")
        return (value, match.group(0)) if value else None


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _make_time(hour_text: str, minute_text: str | None) -> time | None:
    hour = int(hour_text)
    minute = int(minute_text or 0)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _parse_brl_decimal(raw: str, *, is_thousands: bool) -> Decimal:
    raw = raw.strip()
    if "," in raw and "." in raw:
        normalized = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        whole, fraction = raw.rsplit(",", 1)
        normalized = whole.replace(".", "") + "." + fraction
    elif "." in raw:
        parts = raw.split(".")
        normalized = "".join(parts) if not is_thousands and len(parts[-1]) == 3 else raw
    else:
        normalized = raw
    amount = Decimal(normalized)
    return amount * 1000 if is_thousands else amount


__all__ = [
    "LocalPortugueseProcurementInterpreter",
    "ProcurementInterpretationPort",
    "SystemClock",
]
