"""Phase 4A results assembler — transforms verified Phase 3 outputs
(equilibrium, propositions, comparative statics, numerical experiments) into
findings, contribution claims, implications, and an immutable
ResearchResultsPackage.

The LLM (`reasoning` role) organizes and interprets results but never invents
mathematical results. Deterministic validation rejects unsupported artifact
IDs, failed propositions, dropped conditions, and global-novelty claims
(normalized or rejected). No paper drafting (Phase 4B).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.equilibrium import EquilibriumAnalysis
from research_harness.research.schemas.gap import GapAnalysis
from research_harness.research.schemas.mechanism import KnowledgeBasis, SelectedMechanism
from research_harness.research.schemas.model import FormalAnalyticalModel
from research_harness.research.schemas.numerical import NumericalExperiment, NumericalResult
from research_harness.research.schemas.proposition import (
    ComparativeStatic,
    Proposition,
    PropositionVerification,
    PropositionVerificationStatus,
)
from research_harness.research.schemas.results import (
    ContributionClaim,
    ContributionType,
    FindingConfidence,
    FindingType,
    ImplicationClaimType,
    ImplicationKind,
    ResearchFinding,
    ResearchImplication,
    ResearchResultsPackage,
    ResultsAssemblyExecution,
    ResultsPackageStatus,
)

logger = logging.getLogger(__name__)

_MAX_VALIDATION_RETRIES = 2

# Sweeping novelty patterns that are normalized (stripped) during assembly
_NOVELTY_PATTERNS = [
    r"\bfirst\s+(study|work|paper|analysis|investigation|time)\b",
    r"\bthe\s+first\s+to\b",
    r"\bwe\s+are\s+the\s+first\b",
    r"\bno\s+prior\s+(study|work|paper|research|analysis)\b",
    r"\bno\s+(other\s+)?(study|work|paper)\s+has\b",
    r"\bnever\s+been\s+(studied|examined|analyzed)\b",
]
_NOVELTY_RE = re.compile("|".join(_NOVELTY_PATTERNS), flags=re.IGNORECASE)


class _FindingItem(BaseModel):
    statement: str
    finding_type: str = FindingType.analytical_result.value
    supporting_proposition_ids: list[str] = Field(default_factory=list)
    supporting_comparative_static_ids: list[str] = Field(default_factory=list)
    supporting_numerical_result_ids: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    confidence: str = FindingConfidence.medium.value
    knowledge_basis: str = KnowledgeBasis.research_inference.value

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("statement must be non-empty")
        return v

    @field_validator("finding_type")
    @classmethod
    def validate_finding_type(cls, v: str) -> str:
        if v not in FindingType.values():
            raise ValueError(f"invalid finding type {v!r}")
        return v

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: str) -> str:
        if v not in FindingConfidence.values():
            raise ValueError(f"invalid confidence {v!r}")
        return v

    @field_validator("knowledge_basis")
    @classmethod
    def validate_knowledge_basis(cls, v: str) -> str:
        if v not in KnowledgeBasis.values():
            raise ValueError(f"invalid knowledge basis {v!r}")
        return v

    model_config = {"extra": "forbid"}


class _ContributionItem(BaseModel):
    claim: str
    contribution_type: str = ContributionType.theoretical.value
    finding_ids: list[str] = Field(default_factory=list)
    advances_literature: str = Field(default="")
    novelty_claim: str | None = None

    @field_validator("claim")
    @classmethod
    def validate_claim(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("claim must be non-empty")
        return v

    @field_validator("contribution_type")
    @classmethod
    def validate_contribution_type(cls, v: str) -> str:
        if v not in ContributionType.values():
            raise ValueError(f"invalid contribution type {v!r}")
        return v

    model_config = {"extra": "forbid"}


class _ImplicationItem(BaseModel):
    text: str
    implication_kind: str = ImplicationKind.theory.value
    claim_type: str = ImplicationClaimType.interpretation.value
    grounded_in_finding_ids: list[str] = Field(default_factory=list)
    note: str | None = None

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text must be non-empty")
        return v

    @field_validator("implication_kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        if v not in ImplicationKind.values():
            raise ValueError(f"invalid implication kind {v!r}")
        return v

    @field_validator("claim_type")
    @classmethod
    def validate_claim_type(cls, v: str) -> str:
        if v not in ImplicationClaimType.values():
            raise ValueError(f"invalid implication claim type {v!r}")
        return v

    model_config = {"extra": "forbid"}


class _AssemblyResponse(BaseModel):
    findings: list[_FindingItem] = Field(default_factory=list)
    contributions: list[_ContributionItem] = Field(default_factory=list)
    implications: list[_ImplicationItem] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class ResultsAssemblerService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        assembler_role: str = "reasoning",
        max_findings: int = 12,
        max_contributions: int = 8,
        max_implications: int = 12,
        max_llm_calls: int = 10,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._assembler_role = assembler_role
        self._max_findings = max_findings
        self._max_contributions = max_contributions
        self._max_implications = max_implications
        self._max_llm_calls = max_llm_calls

    @property
    def service_id(self) -> str:
        return "research.results_assembler"

    async def assemble(self, numerical_experiment_id: str) -> str:
        """Assemble a ResearchResultsPackage from a numerical experiment.

        Returns the ResultsAssemblyExecution artifact id.
        """
        # Idempotency: reuse the completed assembly for the same experiment + role
        existing = await self._store.list(artifact_type="results_assembly_execution")
        for env in existing:
            try:
                ex = ResultsAssemblyExecution.model_validate(env.payload)
                if (
                    ex.numerical_experiment_id == numerical_experiment_id
                    and ex.model_role == self._assembler_role
                    and ex.completed_at is not None
                    and ex.findings_created > 0
                ):
                    return env.artifact_id
            except Exception:
                continue

        context = await self._load_context(numerical_experiment_id)

        parsed: _AssemblyResponse | None = None
        finding_ids: list[str] = []
        contribution_ids: list[str] = []
        implication_ids: list[str] = []
        errors: list[str] = []
        for attempt in range(1 + _MAX_VALIDATION_RETRIES):
            try:
                response = await self._call_model(context, errors)
                parsed = _AssemblyResponse.model_validate(response)
                finding_ids = await self._persist_findings(context, parsed)
                contribution_ids = await self._persist_contributions(context, parsed, finding_ids)
                implication_ids = await self._persist_implications(context, parsed, finding_ids)
                break
            except ValueError as e:
                if attempt >= _MAX_VALIDATION_RETRIES:
                    raise
                logger.info("assembly validation rejected (%s); retrying with feedback", e)
                errors.append(str(e))

        assert parsed is not None
        package_id = await self._persist_package(
            context, parsed, finding_ids, contribution_ids, implication_ids
        )

        exec_record = ResultsAssemblyExecution(
            numerical_experiment_id=numerical_experiment_id,
            equilibrium_analysis_id=context["equilibrium_analysis_id"],
            model_id=context["model_id"],
            findings_created=len(finding_ids),
            contributions_created=len(contribution_ids),
            implications_created=len(implication_ids),
            novelty_claims_normalized=context["novelty_normalized"],
            counts={
                "findings": len(finding_ids),
                "contributions": len(contribution_ids),
                "implications": len(implication_ids),
            },
            model_role=self._assembler_role,
            started_at=context["started_at"],
            completed_at=datetime.now(UTC),
        )
        exec_id = str(uuid.uuid4())
        exec_env = ArtifactEnvelope.create(
            payload=exec_record,
            artifact_type="results_assembly_execution",
            producer="research.results_assembler",
            artifact_id=exec_id,
        )
        await self._store.put(exec_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=package_id,
                target_artifact_id=exec_id,
                producer="research.results_assembler",
            )
        )
        logger.info(
            "assembled %d findings, %d contributions, %d implications (package %s)",
            len(finding_ids),
            len(contribution_ids),
            len(implication_ids),
            package_id,
        )
        return exec_id

    # ------------------------------------------------------------------
    # Context loading
    # ------------------------------------------------------------------

    async def _load_context(self, numerical_experiment_id: str) -> dict[str, Any]:
        exp_env = await self._store.get(numerical_experiment_id)
        exp = exp_env.parse_payload(NumericalExperiment)
        candidate_id = exp.equilibrium_candidate_id
        model_id = exp.model_id

        eq_analysis_id = None
        for env in await self._store.list(artifact_type="equilibrium_analysis"):
            try:
                a = env.parse_payload(EquilibriumAnalysis)
            except Exception:
                continue
            if a.selected_candidate_id == candidate_id:
                eq_analysis_id = env.artifact_id
                break
        if eq_analysis_id is None:
            raise ValueError(
                f"no EquilibriumAnalysis selects candidate {candidate_id} of experiment"
            )

        model = (await self._store.get(model_id)).parse_payload(FormalAnalyticalModel)
        mechanism = (await self._store.get(model.selected_mechanism_id)).parse_payload(
            SelectedMechanism
        )
        gap_id = mechanism.gap_id

        # research question from the gap analysis that produced the gap
        research_question_id = None
        for env in await self._store.list(artifact_type="gap_analysis"):
            try:
                g = env.parse_payload(GapAnalysis)
            except Exception:
                continue
            if gap_id in g.gap_ids and g.research_question_id:
                research_question_id = g.research_question_id
                break

        # Verified / failed proposition map (PropositionVerification is authoritative)
        verified_props: dict[str, Proposition] = {}
        failed_prop_ids: set[str] = set()
        for env in await self._store.list(artifact_type="proposition"):
            try:
                p = env.parse_payload(Proposition)
            except Exception:
                continue
            if p.model_id != model_id:
                continue
            latest: PropositionVerification | None = None
            for venv in await self._store.list(artifact_type="proposition_verification"):
                try:
                    v = venv.parse_payload(PropositionVerification)
                except Exception:
                    continue
                if v.proposition_id == env.artifact_id:
                    if latest is None or v.created_at >= latest.created_at:
                        latest = v
            if latest is None:
                continue
            if latest.status in (
                PropositionVerificationStatus.verified,
                PropositionVerificationStatus.conditionally_verified,
            ):
                verified_props[env.artifact_id] = p
            elif latest.status == PropositionVerificationStatus.failed:
                failed_prop_ids.add(env.artifact_id)

        statics: dict[str, ComparativeStatic] = {}
        for env in await self._store.list(artifact_type="comparative_static"):
            try:
                s = env.parse_payload(ComparativeStatic)
            except Exception:
                continue
            if s.model_id == model_id and s.equilibrium_candidate_id == candidate_id:
                statics[env.artifact_id] = s

        results: dict[str, Any] = {}
        for rid in exp.results:
            try:
                results[rid] = (await self._store.get(rid)).parse_payload(NumericalResult)
            except Exception:
                pass

        return {
            "experiment_id": numerical_experiment_id,
            "equilibrium_analysis_id": eq_analysis_id,
            "candidate_id": candidate_id,
            "model_id": model_id,
            "model": model,
            "mechanism": mechanism,
            "gap_id": gap_id,
            "research_question_id": research_question_id,
            "verified_props": verified_props,
            "failed_prop_ids": failed_prop_ids,
            "statics": statics,
            "result_ids": list(exp.results),
            "experiment": exp,
            "started_at": datetime.now(UTC),
            "novelty_normalized": 0,
        }

    # ------------------------------------------------------------------
    # Model call
    # ------------------------------------------------------------------

    async def _call_model(
        self, context: dict[str, Any], prior_errors: list[str] | None = None
    ) -> dict[str, Any]:
        prompt = self._build_prompt(context, prior_errors)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You assemble research findings, contribution claims, and "
                        "implications from VERIFIED results only. Return valid JSON "
                        "matching the schema. Never invent mathematical results. "
                        "Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_schema(),
            temperature=0.0,
            metadata={"experiment_id": context["experiment_id"]},
        )
        try:
            response = await self._router.complete(self._assembler_role, request)
            return json.loads(response.message.content or "")
        except Exception as e:
            raise ValueError(f"results assembly model call failed: {e}") from e

    def _build_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "statement": {"type": "string"},
                            "finding_type": {"type": "string", "enum": FindingType.values()},
                            "supporting_proposition_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "supporting_comparative_static_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "supporting_numerical_result_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "conditions": {"type": "array", "items": {"type": "string"}},
                            "confidence": {"type": "string", "enum": FindingConfidence.values()},
                            "knowledge_basis": {"type": "string", "enum": KnowledgeBasis.values()},
                        },
                        "required": ["statement", "finding_type"],
                        "additionalProperties": False,
                    },
                },
                "contributions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim": {"type": "string"},
                            "contribution_type": {
                                "type": "string",
                                "enum": ContributionType.values(),
                            },
                            "finding_ids": {"type": "array", "items": {"type": "string"}},
                            "advances_literature": {"type": "string"},
                            "novelty_claim": {"type": "string"},
                        },
                        "required": ["claim", "contribution_type", "finding_ids"],
                        "additionalProperties": False,
                    },
                },
                "implications": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "implication_kind": {
                                "type": "string",
                                "enum": ImplicationKind.values(),
                            },
                            "claim_type": {"type": "string", "enum": ImplicationClaimType.values()},
                            "grounded_in_finding_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "note": {"type": "string"},
                        },
                        "required": ["text", "implication_kind", "claim_type"],
                        "additionalProperties": False,
                    },
                },
                "limitations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["findings", "contributions", "implications"],
            "additionalProperties": False,
        }

    def _build_prompt(self, context: dict[str, Any], prior_errors: list[str] | None = None) -> str:
        model: FormalAnalyticalModel = context["model"]
        mechanism: SelectedMechanism = context["mechanism"]
        exp: NumericalExperiment = context["experiment"]
        cand_lines = "\n".join(
            f"  - {p.actor_id}: {p.expression.expression}" for p in model.payoffs
        )
        prop_lines = (
            "\n".join(
                f"  [{pid}] {p.statement} | conditions: {'; '.join(p.conditions) or '-'}"
                for pid, p in sorted(context["verified_props"].items())
            )
            or "  (none)"
        )
        static_lines = (
            "\n".join(
                f"  [{sid}] d{s.outcome_variable}/d{s.parameter} sign {s.sign.value}"
                + (f" | conditions: {'; '.join(s.conditions)}" if s.conditions else "")
                for sid, s in sorted(context["statics"].items())
            )
            or "  (none)"
        )
        result_ids_line = ", ".join(context["result_ids"][:20])
        more = max(0, len(context["result_ids"]) - 20)
        result_ids_line += f" … ({more} more)" if more else ""
        return f"""Assemble findings, contribution claims, and implications from verified results.

Gap: {context["gap_id"]}
Mechanism: {mechanism.name} — {mechanism.description[:200]}
Model: {model.title} — actors: {", ".join(a.actor_id for a in model.actors)}
Payoffs:
{cand_lines}

VERIFIED propositions (IDs authoritative; cite ONLY these ids, and NEVER any
failed proposition):
{prop_lines}

VERIFIED comparative statics (IDs authoritative):
{static_lines}

Numerical experiment: {exp.summary}
Numerical result ids (cite ONLY from this set): {result_ids_line}

Rules:
- Every finding must cite at least one verified proposition, comparative
  static, or numerical result; never a failed proposition.
- conditions of a finding must include ALL conditions of every referenced
  proposition and comparative static — never drop them.
- In contributions and implications, reference your own findings
  POSITIONALLY as FINDING0, FINDING1, ... (the k-th finding in the findings
  array of this response). Never invent other ids.
- Contribution claims must be corpus-bounded; do NOT claim global novelty
  ('first study', 'no prior work') — such claims are stripped automatically.
- Implications: distinguish mathematically established results, economic
  interpretation, managerial implications, and speculations/hypotheses via
  claim_type; policy implications only when genuinely supported.
- Return valid JSON only, no chain-of-thought.

{self._prior_errors_text(prior_errors)}
"""

    def _prior_errors_text(self, prior_errors: list[str] | None) -> str:
        if not prior_errors:
            return ""
        lines = "\n".join(f"  - {err}" for err in prior_errors)
        return (
            "Your previous attempt was REJECTED by deterministic validation:\n"
            f"{lines}\n"
            "Fix ALL occurrences (every finding/contribution/implication) and "
            "re-issue the full corrected response."
        )

    # ------------------------------------------------------------------
    # Deterministic validation + persistence
    # ------------------------------------------------------------------

    async def _persist_findings(
        self, context: dict[str, Any], parsed: _AssemblyResponse
    ) -> list[str]:
        verified = set(context["verified_props"])
        statics = set(context["statics"])
        results = set(context["result_ids"])
        failed = context["failed_prop_ids"]
        out: list[str] = []
        for item in parsed.findings[: self._max_findings]:
            self._validate_finding(context, item, verified, statics, results, failed)
            finding = ResearchFinding(
                model_id=context["model_id"],
                equilibrium_candidate_id=context["candidate_id"],
                statement=item.statement,
                finding_type=FindingType(item.finding_type),
                supporting_proposition_ids=list(item.supporting_proposition_ids),
                supporting_comparative_static_ids=list(item.supporting_comparative_static_ids),
                supporting_numerical_result_ids=list(item.supporting_numerical_result_ids),
                conditions=list(item.conditions),
                confidence=FindingConfidence(item.confidence),
                knowledge_basis=KnowledgeBasis(item.knowledge_basis),
            )
            f_env = ArtifactEnvelope.create(
                payload=finding,
                artifact_type="research_finding",
                producer=f"research.results_assembler:{self._assembler_role}",
            )
            await self._store.put(f_env)
            for pid in item.supporting_proposition_ids:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=pid,
                        target_artifact_id=f_env.artifact_id,
                        producer="research.results_assembler",
                    )
                )
            for sid in item.supporting_comparative_static_ids:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=sid,
                        target_artifact_id=f_env.artifact_id,
                        producer="research.results_assembler",
                    )
                )
            for rid in item.supporting_numerical_result_ids:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=rid,
                        target_artifact_id=f_env.artifact_id,
                        producer="research.results_assembler",
                    )
                )
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=context["candidate_id"],
                    target_artifact_id=f_env.artifact_id,
                    producer="research.results_assembler",
                )
            )
            out.append(f_env.artifact_id)
        return out

    def _validate_finding(
        self,
        context: dict[str, Any],
        item: _FindingItem,
        verified: set[str],
        statics: set[str],
        results: set[str],
        failed: set[str],
    ) -> None:
        for pid in item.supporting_proposition_ids:
            if pid in failed:
                raise ValueError(f"failed proposition {pid} cannot support a finding")
            if pid not in verified:
                raise ValueError(
                    f"proposition {pid} is not verified/conditionally verified for this model"
                )
        for sid in item.supporting_comparative_static_ids:
            if sid not in statics:
                raise ValueError(f"unsupported comparative static id {sid}")
        for rid in item.supporting_numerical_result_ids:
            if rid not in results:
                raise ValueError(f"unsupported numerical result id {rid}")
        if (
            not item.supporting_proposition_ids
            and not item.supporting_comparative_static_ids
            and not item.supporting_numerical_result_ids
        ):
            raise ValueError("finding must cite at least one verified support")
        required_conditions: list[str] = []
        for pid in item.supporting_proposition_ids:
            prop = context["verified_props"][pid]
            required_conditions.extend(prop.conditions)
        for sid in item.supporting_comparative_static_ids:
            stat = context["statics"][sid]
            required_conditions.extend(stat.conditions)
        missing = [c for c in required_conditions if not any(c in cond for cond in item.conditions)]
        if missing:
            raise ValueError(
                f"finding drops required conditions: {missing} "
                f"(must include conditions of all referenced supports)"
            )

    async def _persist_contributions(
        self, context: dict[str, Any], parsed: _AssemblyResponse, finding_ids: list[str]
    ) -> list[str]:
        finding_set = set(finding_ids)
        out: list[str] = []
        for item in parsed.contributions[: self._max_contributions]:
            if not item.finding_ids:
                raise ValueError("contribution claim must reference at least one finding")
            refs = self._resolve_finding_refs(item.finding_ids, finding_ids, finding_set)
            unsupported = [fid for fid in refs if fid not in finding_set]
            if unsupported:
                raise ValueError(f"contribution references unknown findings: {unsupported}")
            claim, normalized = self._normalize_novelty(item.claim)
            novelty = item.novelty_claim
            novelty_note = None
            if novelty and _NOVELTY_RE.search(novelty):
                novelty = _NOVELTY_RE.sub("", novelty).strip() or None
                normalized = True
            if normalized:
                context["novelty_normalized"] += 1
                novelty_note = "sweeping novelty claim normalized during assembly"
            claim_artifact = ContributionClaim(
                gap_id=context["gap_id"],
                finding_ids=list(refs),
                claim=claim,
                contribution_type=ContributionType(item.contribution_type),
                advances_literature=item.advances_literature,
                novelty_claim=novelty,
                novelty_normalized=normalized,
                metadata={"novelty_note": novelty_note} if novelty_note else {},
            )
            c_env = ArtifactEnvelope.create(
                payload=claim_artifact,
                artifact_type="contribution_claim",
                producer=f"research.results_assembler:{self._assembler_role}",
            )
            await self._store.put(c_env)
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=context["gap_id"],
                    target_artifact_id=c_env.artifact_id,
                    producer="research.results_assembler",
                )
            )
            for fid in refs:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=fid,
                        target_artifact_id=c_env.artifact_id,
                        producer="research.results_assembler",
                    )
                )
            out.append(c_env.artifact_id)
        return out

    def _normalize_novelty(self, claim: str) -> tuple[str, bool]:
        if _NOVELTY_RE.search(claim):
            return _NOVELTY_RE.sub("", claim).strip(), True
        return claim, False

    def _resolve_finding_refs(
        self, refs: list[str], finding_ids: list[str], finding_set: set[str]
    ) -> list[str]:
        """Map positional tokens (FINDINGk = k-th finding of this response) to ids.

        The LLM cannot know finding UUIDs when it responds, so contributions and
        implications reference findings positionally; resolution is deterministic.
        """
        import re

        out: list[str] = []
        for ref in refs:
            m = re.fullmatch(r"FINDING(\d+)", ref)
            if m:
                idx = int(m.group(1))
                if idx >= len(finding_ids):
                    raise ValueError(f"finding token {ref} out of range")
                out.append(finding_ids[idx])
            else:
                out.append(ref)
        return out

    async def _persist_implications(
        self, context: dict[str, Any], parsed: _AssemblyResponse, finding_ids: list[str]
    ) -> list[str]:
        finding_set = set(finding_ids)
        out: list[str] = []
        for item in parsed.implications[: self._max_implications]:
            refs = self._resolve_finding_refs(
                item.grounded_in_finding_ids, finding_ids, finding_set
            )
            unsupported = [fid for fid in refs if fid not in finding_set]
            if unsupported:
                raise ValueError(f"implication references unknown findings: {unsupported}")
            impl = ResearchImplication(
                implication_kind=ImplicationKind(item.implication_kind),
                claim_type=ImplicationClaimType(item.claim_type),
                text=item.text,
                grounded_in_finding_ids=refs,
                note=item.note,
            )
            i_env = ArtifactEnvelope.create(
                payload=impl,
                artifact_type="research_implication",
                producer=f"research.results_assembler:{self._assembler_role}",
            )
            await self._store.put(i_env)
            for fid in refs:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=fid,
                        target_artifact_id=i_env.artifact_id,
                        producer="research.results_assembler",
                    )
                )
            out.append(i_env.artifact_id)
        return out

    async def _persist_package(
        self,
        context: dict[str, Any],
        parsed: _AssemblyResponse,
        finding_ids: list[str],
        contribution_ids: list[str],
        implication_ids: list[str],
    ) -> str:
        package = ResearchResultsPackage(
            research_question_id=context["research_question_id"],
            gap_id=context["gap_id"],
            selected_mechanism_id=context["model"].selected_mechanism_id,
            model_id=context["model_id"],
            equilibrium_analysis_id=context["equilibrium_analysis_id"],
            equilibrium_candidate_id=context["candidate_id"],
            numerical_experiment_id=context["experiment_id"],
            finding_ids=finding_ids,
            contribution_claim_ids=contribution_ids,
            implication_ids=implication_ids,
            limitations=list(parsed.limitations),
            status=ResultsPackageStatus.assembled,
            summary=(
                f"{len(finding_ids)} findings, {len(contribution_ids)} contributions, "
                f"{len(implication_ids)} implications, {len(parsed.limitations)} limitations"
            ),
            model_role=self._assembler_role,
            metadata={"robustness_ids": list(context["experiment"].robustness)},
        )
        p_env = ArtifactEnvelope.create(
            payload=package,
            artifact_type="results_package",
            producer=f"research.results_assembler:{self._assembler_role}",
        )
        await self._store.put(p_env)
        for cid in contribution_ids:
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=cid,
                    target_artifact_id=p_env.artifact_id,
                    producer="research.results_assembler",
                )
            )
        for target in (
            context["gap_id"],
            context["model"].selected_mechanism_id,
            context["model_id"],
            context["equilibrium_analysis_id"],
        ):
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=target,
                    target_artifact_id=p_env.artifact_id,
                    producer="research.results_assembler",
                )
            )
        return p_env.artifact_id


class ResultsAssemblerPlugin(Plugin):
    def __init__(self, assembler_role: str | None = None) -> None:
        self._assembler_role_override = assembler_role
        self._service: ResultsAssemblerService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.results_assembler",
            version="0.1.0",
            plugin_type="research",
            description="Results assembly: findings, contributions, implications, package (Phase 4A)",
            provides=["results_assembler.default"],
            requires=["model_router.default", "artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        research_cfg: dict[str, Any] = {}
        if "research" in cfg and isinstance(cfg["research"], dict):
            research_cfg = (
                cfg["research"].get("results", {})
                if isinstance(cfg["research"].get("results"), dict)
                else {}
            )
        assembler_role = (
            self._assembler_role_override or research_cfg.get("assembler_role") or "reasoning"
        )
        max_findings = int(research_cfg.get("max_findings", 12))
        max_contributions = int(research_cfg.get("max_contributions", 8))
        max_implications = int(research_cfg.get("max_implications", 12))
        max_llm_calls = int(research_cfg.get("max_llm_calls", 10))
        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        self._service = ResultsAssemblerService(
            model_router=router,
            artifact_store=store,
            assembler_role=str(assembler_role),
            max_findings=max_findings,
            max_contributions=max_contributions,
            max_implications=max_implications,
            max_llm_calls=max_llm_calls,
        )
        ctx.register("results_assembler.default", self._service)
