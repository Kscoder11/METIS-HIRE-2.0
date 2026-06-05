"""
METIS Evaluator

Main orchestrator that combines parsers and analyzers to produce
METIS-CORE evaluation output.

Fallback strategy:
  - Primary: METIS custom resume_parser (full NLP pipeline)
  - Fallback: regex-based parser from ai_service (always available)

Confidence scores are emitted for every field so the HR UI can flag
fields that need manual verification.
"""

import json
from dataclasses import dataclass, field

from . import jd_parser
from . import resume_parser
from . import github_analyzer
from . import portfolio_analyzer
from . import scoring_engine


# ── Confidence thresholds ────────────────────────────────────────────────────
# Number of resume sections that must be populated to award "high" confidence.
_HIGH_CONFIDENCE_SECTION_COUNT = 4
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CandidateInput:
    """Input data for candidate evaluation."""
    resume_text: str
    github_url: str | None = None
    linkedin_url: str | None = None
    portfolio_url: str | None = None


@dataclass
class EvaluationContext:
    """Context for evaluation including all parsed data."""
    resume_data: dict = field(default_factory=dict)
    github_data: dict = field(default_factory=dict)
    portfolio_data: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    used_fallback_parser: bool = False


class MetisEvaluator:
    """
    METIS-CORE Evaluator

    Orchestrates the complete candidate evaluation pipeline.
    Returns strict JSON matching METIS output contract, including
    per-field confidence scores for HR review.
    """

    def __init__(self):
        self.context = EvaluationContext()

    def parse_resume(self, resume_text: str) -> dict:
        """
        Parse resume text using the primary METIS parser.

        Falls back to the regex-based parser in ai_service if the primary
        parser raises any exception. The fallback is always noted in the
        result so the UI can warn HR.
        """
        # ── Primary parser ───────────────────────────────────────────────────
        try:
            self.context.resume_data = resume_parser.parse(resume_text)
            self.context.used_fallback_parser = False
            return self.context.resume_data
        except Exception as primary_error:
            self.context.errors.append(
                f"Primary resume parser failed: {primary_error}. "
                "Falling back to regex parser."
            )
            print(f"[WARN] Primary resume parser failed: {primary_error}")

        # ── Fallback: regex + keyword extraction ─────────────────────────────
        try:
            from services.ai_service import ai_service as _ai_service
            fallback_data = _ai_service.parse_resume(resume_text)
            self.context.resume_data = fallback_data
            self.context.used_fallback_parser = True
            self.context.errors.append(
                "Fallback regex parser was used — some fields may be inaccurate."
            )
            print("[INFO] Fallback regex parser succeeded.")
            return self.context.resume_data
        except Exception as fallback_error:
            self.context.errors.append(f"Fallback parser also failed: {fallback_error}")
            print(f"[ERROR] Both parsers failed: {fallback_error}")
            return {}

    def analyze_github(self, github_url: str) -> dict:
        """Analyze GitHub profile and store in context."""
        if not github_url:
            return {}

        try:
            self.context.github_data = github_analyzer.analyze(github_url)
            return self.context.github_data
        except ValueError as e:
            self.context.errors.append(f"GitHub error: {str(e)}")
            return {}
        except Exception as e:
            self.context.errors.append(f"GitHub analysis failed: {str(e)}")
            return {}

    def analyze_portfolio(self, portfolio_url: str) -> dict:
        """Analyze portfolio website and store in context."""
        if not portfolio_url:
            return {}

        try:
            self.context.portfolio_data = portfolio_analyzer.analyze(portfolio_url)
            return self.context.portfolio_data
        except Exception as e:
            self.context.errors.append(f"Portfolio analysis failed: {str(e)}")
            return {}

    def _compute_confidence_scores(self, resume_data: dict, result: dict) -> dict:
        """
        Compute field-level confidence scores for the parsed resume.

        Returns a dict of {field: confidence_score (0–1)} that the HR UI
        can use to highlight fields that need manual verification.

        Confidence is degraded when:
        - The fallback parser was used (lower fidelity extraction)
        - A field is empty or very short
        - The METIS model's own confidence_level is not "high"
        """
        base = 0.5 if self.context.used_fallback_parser else 0.9
        model_confidence = result.get('confidence_level', 'medium')
        if model_confidence == 'high':
            base = min(base, 0.95)
        elif model_confidence == 'low':
            base = min(base, 0.4)

        def field_score(value) -> float:
            if not value:
                return 0.0
            if isinstance(value, list):
                return base if len(value) > 0 else 0.0
            if isinstance(value, str):
                return base if len(value) > 5 else base * 0.5
            return base

        fields = ['skills', 'education', 'experience', 'projects',
                  'certifications', 'email', 'phone', 'linkedinUrl', 'githubUrl']
        return {f: round(field_score(resume_data.get(f)), 2) for f in fields}

    def evaluate(self, candidate: CandidateInput) -> dict:
        """
        Run METIS-CORE evaluation for a candidate.

        Args:
            candidate: CandidateInput with resume and optional URLs

        Returns:
            Strict JSON matching METIS output contract, augmented with:
            - parsing_confidence: dict of per-field confidence scores
            - used_fallback_parser: bool
            - parsing_warnings: list of issues for HR review
        """
        if not candidate.resume_text:
            self.context.errors.append("Resume text is required")
            return {
                "model": "metis_core_v1",
                "overall_score": 0,
                "section_scores": {},
                "strength_signals": [],
                "risk_signals": ["No resume provided"],
                "ats_flags": [],
                "confidence_level": "low",
                "confidence_score": 0.0,
                "parsing_confidence": {},
                "used_fallback_parser": False,
                "parsing_warnings": ["No resume text supplied."],
                "final_reasoning": "Cannot evaluate without resume.",
            }

        self.parse_resume(candidate.resume_text)

        if candidate.github_url:
            self.analyze_github(candidate.github_url)

        if candidate.portfolio_url:
            self.analyze_portfolio(candidate.portfolio_url)

        # Run METIS-CORE scoring engine
        result = scoring_engine.evaluate(
            resume_data=self.context.resume_data,
            github_data=self.context.github_data if self.context.github_data else None,
            portfolio_data=self.context.portfolio_data if self.context.portfolio_data else None,
        )

        # ── Confidence augmentation ──────────────────────────────────────────
        parsing_confidence = self._compute_confidence_scores(self.context.resume_data, result)

        # Overall confidence: average of non-zero field scores, reduced for fallback
        non_zero = [v for v in parsing_confidence.values() if v > 0]
        overall_conf = round(sum(non_zero) / len(non_zero), 2) if non_zero else 0.0
        if self.context.used_fallback_parser:
            overall_conf = round(overall_conf * 0.7, 2)  # Penalise fallback

        # Build human-readable warnings for the HR dashboard
        warnings = list(self.context.errors)
        low_conf_fields = [f for f, v in parsing_confidence.items() if 0 < v < 0.5]
        if low_conf_fields:
            warnings.append(
                f"Low confidence fields (manual review recommended): {', '.join(low_conf_fields)}"
            )
        # ─────────────────────────────────────────────────────────────────────

        result['parsing_confidence'] = parsing_confidence
        result['confidence_score'] = overall_conf
        result['used_fallback_parser'] = self.context.used_fallback_parser
        result['parsing_warnings'] = warnings

        return result


def evaluate_candidate(
    resume_text: str,
    github_url: str | None = None,
    linkedin_url: str | None = None,
    portfolio_url: str | None = None,
    jd_text: str | None = None,  # Ignored per spec unless explicitly provided
) -> dict:
    """
    Convenience function for single candidate evaluation.

    Args:
        resume_text: Resume text (primary source)
        github_url: Optional GitHub profile URL
        linkedin_url: Optional LinkedIn profile URL (not implemented)
        portfolio_url: Optional portfolio website URL
        jd_text: Job description (ignored unless explicitly needed)

    Returns:
        Strict METIS JSON output with confidence metadata
    """
    evaluator = MetisEvaluator()

    candidate = CandidateInput(
        resume_text=resume_text,
        github_url=github_url,
        linkedin_url=linkedin_url,
        portfolio_url=portfolio_url,
    )

    return evaluator.evaluate(candidate)


def format_result_json(result: dict, indent: int = 2) -> str:
    """Format evaluation result as pretty JSON."""
    return json.dumps(result, indent=indent, ensure_ascii=False)

