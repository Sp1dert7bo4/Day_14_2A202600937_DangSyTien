"""
Day 14 — AI Evaluation & Benchmarking Pipeline
AICB-P1: AI Practical Competency Program, Phase 1
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Task 1 — Data Models (Golden Dataset + Evaluation Results)
# ---------------------------------------------------------------------------

@dataclass
class QAPair:
    """A question-answer pair for evaluation."""
    question: str
    expected_answer: str
    context: str = ""
    metadata: dict = field(default_factory=dict)
    retrieved_contexts: list = field(default_factory=list)


@dataclass
class EvalResult:
    """Evaluation result for a single Q&A pair."""
    qa_pair: QAPair
    actual_answer: str
    faithfulness: float
    relevance: float
    completeness: float
    passed: bool
    failure_type: str | None = None
    context_precision: float | None = None
    context_recall: float | None = None

    def overall_score(self) -> float:
        """Compute the average of faithfulness, relevance, and completeness."""
        return (self.faithfulness + self.relevance + self.completeness) / 3.0


# ---------------------------------------------------------------------------
# Task 2 — RAGAS Evaluator (Simplified word-overlap heuristic)
# ---------------------------------------------------------------------------

STOPWORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "with", "as", "by", "and", "or",
    "it", "its", "this", "that", "these", "those", "from", "into", "than",
}

def _tokenize(text: str) -> set[str]:
    """Lowercase word tokenization, ignoring punctuation and stopwords."""
    if not text:
        return set()
    tokens = re.findall(r"\b\w+\b", text.lower())
    return {t for t in tokens if t not in STOPWORDS}


class RAGASEvaluator:
    """Evaluates RAG pipeline outputs using RAGAS-inspired heuristics."""

    def evaluate_faithfulness(self, answer: str, context: str) -> float:
        if not answer:
            return 1.0
        answer_tokens = _tokenize(answer)
        if not answer_tokens:
            return 1.0
        context_tokens = _tokenize(context)
        score = len(answer_tokens & context_tokens) / len(answer_tokens)
        return min(max(score, 0.0), 1.0)

    def evaluate_relevance(self, answer: str, question: str) -> float:
        if not question:
            return 1.0
        question_tokens = _tokenize(question)
        if not question_tokens:
            return 1.0
        answer_tokens = _tokenize(answer)
        score = len(answer_tokens & question_tokens) / len(question_tokens)
        return min(max(score, 0.0), 1.0)

    def evaluate_completeness(self, answer: str, expected: str) -> float:
        if not expected:
            return 1.0
        expected_tokens = _tokenize(expected)
        if not expected_tokens:
            return 1.0
        answer_tokens = _tokenize(answer)
        score = len(answer_tokens & expected_tokens) / len(expected_tokens)
        return min(max(score, 0.0), 1.0)

    def evaluate_context_recall(self, contexts: list[str], expected: str) -> float:
        if not expected:
            return 1.0
        expected_tokens = _tokenize(expected)
        if not expected_tokens:
            return 1.0
        union_tokens = set()
        for chunk in contexts:
            union_tokens.update(_tokenize(chunk))
        score = len(expected_tokens & union_tokens) / len(expected_tokens)
        return min(max(score, 0.0), 1.0)

    def evaluate_context_precision(
        self,
        contexts: list[str],
        expected: str,
        relevance_threshold: float = 0.1,
    ) -> float:
        if not expected:
            return 1.0
        expected_tokens = _tokenize(expected)
        if not expected_tokens:
            return 1.0
        if not contexts:
            return 0.0
        
        relevant_flags = []
        for chunk in contexts:
            chunk_tokens = _tokenize(chunk)
            score = len(chunk_tokens & expected_tokens) / len(expected_tokens) if expected_tokens else 1.0
            relevant_flags.append(score >= relevance_threshold)
        
        num_relevant = sum(1 for r in relevant_flags if r)
        if num_relevant == 0:
            return 0.0
        
        sum_precision = 0.0
        relevant_count_so_far = 0
        for k, is_relevant in enumerate(relevant_flags, start=1):
            if is_relevant:
                relevant_count_so_far += 1
                precision_at_k = relevant_count_so_far / k
                sum_precision += precision_at_k
        
        ap = sum_precision / num_relevant
        return min(max(ap, 0.0), 1.0)

    def run_full_eval(
        self,
        answer: str,
        question: str,
        context: str,
        expected: str,
    ) -> EvalResult:
        faithfulness = self.evaluate_faithfulness(answer, context)
        relevance = self.evaluate_relevance(answer, question)
        completeness = self.evaluate_completeness(answer, expected)
        
        passed = faithfulness >= 0.5 and relevance >= 0.5 and completeness >= 0.5
        
        failure_type = None
        if not passed:
            if faithfulness < 0.3:
                failure_type = "hallucination"
            elif relevance < 0.3:
                failure_type = "irrelevant"
            elif completeness < 0.3:
                failure_type = "incomplete"
            else:
                failure_type = "off_topic"
                
        qa_pair = QAPair(question=question, expected_answer=expected, context=context)
        return EvalResult(
            qa_pair=qa_pair,
            actual_answer=answer,
            faithfulness=faithfulness,
            relevance=relevance,
            completeness=completeness,
            passed=passed,
            failure_type=failure_type,
            context_precision=None,
            context_recall=None
        )


def rerank_by_overlap(contexts: list[str], query: str) -> list[str]:
    return sorted(contexts, key=lambda c: len(_tokenize(c) & _tokenize(query)), reverse=True)


# ---------------------------------------------------------------------------
# Task 3 — LLM Judge
# ---------------------------------------------------------------------------

class LLMJudge:
    def __init__(self, judge_llm_fn: Callable[[str], str]) -> None:
        self.judge_llm_fn = judge_llm_fn

    def score_response(
        self,
        question: str,
        answer: str,
        rubric: dict[str, Any],
    ) -> dict[str, Any]:
        rubric_desc = "\n".join(f"- {k}: {v}" for k, v in rubric.items())
        prompt = (
            f"Question: {question}\n"
            f"Answer: {answer}\n"
            f"Rubric:\n{rubric_desc}\n"
            f"Please rate the answer for each criterion on a scale of 0 to 1 based on the rubric.\n"
            f"Return only a JSON object like: {{'criterion': score_value}}"
        )
        try:
            response_str = self.judge_llm_fn(prompt)
            if "```json" in response_str:
                response_str = response_str.split("```json")[1].split("```")[0].strip()
            elif "```" in response_str:
                response_str = response_str.split("```")[1].split("```")[0].strip()
            scores = json.loads(response_str)
            final_scores = {}
            for k in rubric:
                final_scores[k] = float(scores.get(k, 0.5))
            return {
                "scores": final_scores,
                "reasoning": response_str
            }
        except Exception as e:
            return {
                "scores": {k: 0.5 for k in rubric},
                "reasoning": f"Failed to parse LLM judge response. Error: {str(e)}"
            }

    def detect_bias(self, scores_batch: list[dict[str, Any]]) -> dict[str, Any]:
        if not scores_batch:
            return {
                "positional_bias": False,
                "leniency_bias": False,
                "severity_bias": False,
            }
        
        item_averages = []
        all_scores = []
        for item in scores_batch:
            scores = item.get("scores", {})
            if scores:
                avg = sum(scores.values()) / len(scores)
                item_averages.append(avg)
                all_scores.extend(scores.values())
            else:
                item_averages.append(0.5)
        
        overall_avg = sum(all_scores) / len(all_scores) if all_scores else 0.5
        
        leniency_bias = overall_avg > 0.8
        severity_bias = overall_avg < 0.3
        
        positional_bias = False
        if len(item_averages) > 1:
            if item_averages[0] > item_averages[-1]:
                positional_bias = True
                
        return {
            "positional_bias": positional_bias,
            "leniency_bias": leniency_bias,
            "severity_bias": severity_bias,
        }


# ---------------------------------------------------------------------------
# Task 4 — Benchmark Runner
# ---------------------------------------------------------------------------

class BenchmarkRunner:
    def run(
        self,
        qa_pairs: list[QAPair],
        agent_fn: Callable[[str], str],
        evaluator: RAGASEvaluator,
    ) -> list[EvalResult]:
        results = []
        for pair in qa_pairs:
            answer = agent_fn(pair.question)
            res = evaluator.run_full_eval(
                answer=answer,
                question=pair.question,
                context=pair.context,
                expected=pair.expected_answer
            )
            res.qa_pair = pair
            if pair.retrieved_contexts:
                res.context_recall = evaluator.evaluate_context_recall(pair.retrieved_contexts, pair.expected_answer)
                res.context_precision = evaluator.evaluate_context_precision(pair.retrieved_contexts, pair.expected_answer)
            results.append(res)
        return results

    def generate_report(self, results: list[EvalResult]) -> dict[str, Any]:
        if not results:
            return {
                "total": 0,
                "passed": 0,
                "pass_rate": 0.0,
                "avg_faithfulness": 0.0,
                "avg_relevance": 0.0,
                "avg_completeness": 0.0,
                "failure_types": {},
            }
        
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        pass_rate = passed / total
        
        avg_faithfulness = sum(r.faithfulness for r in results) / total
        avg_relevance = sum(r.relevance for r in results) / total
        avg_completeness = sum(r.completeness for r in results) / total
        
        failure_types = {}
        for r in results:
            if not r.passed and r.failure_type:
                failure_types[r.failure_type] = failure_types.get(r.failure_type, 0) + 1
                
        return {
            "total": total,
            "passed": passed,
            "pass_rate": pass_rate,
            "avg_faithfulness": avg_faithfulness,
            "avg_relevance": avg_relevance,
            "avg_completeness": avg_completeness,
            "failure_types": failure_types,
        }

    def run_regression(self, new_results: list[EvalResult], baseline_results: list[EvalResult]) -> dict:
        if not new_results or not baseline_results:
            return {
                'new_avg_faithfulness': 0.0,
                'new_avg_relevance': 0.0,
                'new_avg_completeness': 0.0,
                'baseline_avg_faithfulness': 0.0,
                'baseline_avg_relevance': 0.0,
                'baseline_avg_completeness': 0.0,
                'regressions': [],
                'passed': True,
            }
        
        new_avg_faithfulness = sum(r.faithfulness for r in new_results) / len(new_results)
        new_avg_relevance = sum(r.relevance for r in new_results) / len(new_results)
        new_avg_completeness = sum(r.completeness for r in new_results) / len(new_results)
        
        baseline_avg_faithfulness = sum(r.faithfulness for r in baseline_results) / len(baseline_results)
        baseline_avg_relevance = sum(r.relevance for r in baseline_results) / len(baseline_results)
        baseline_avg_completeness = sum(r.completeness for r in baseline_results) / len(baseline_results)
        
        regressions = []
        if baseline_avg_faithfulness - new_avg_faithfulness > 0.05:
            regressions.append('faithfulness')
        if baseline_avg_relevance - new_avg_relevance > 0.05:
            regressions.append('relevance')
        if baseline_avg_completeness - new_avg_completeness > 0.05:
            regressions.append('completeness')
            
        passed = len(regressions) == 0
        
        return {
            'new_avg_faithfulness': new_avg_faithfulness,
            'new_avg_relevance': new_avg_relevance,
            'new_avg_completeness': new_avg_completeness,
            'baseline_avg_faithfulness': baseline_avg_faithfulness,
            'baseline_avg_relevance': baseline_avg_relevance,
            'baseline_avg_completeness': baseline_avg_completeness,
            'regressions': regressions,
            'passed': passed,
        }

    def identify_failures(
        self,
        results: list[EvalResult],
        threshold: float = 0.5,
    ) -> list[EvalResult]:
        failures = []
        for r in results:
            if r.faithfulness < threshold or r.relevance < threshold or r.completeness < threshold:
                failures.append(r)
        return failures


# ---------------------------------------------------------------------------
# Task 5 — Failure Analyzer
# ---------------------------------------------------------------------------

class FailureAnalyzer:
    def categorize_failures(self, failures: list[EvalResult]) -> dict[str, int]:
        categories = {}
        for r in failures:
            ftype = r.failure_type or "unknown"
            categories[ftype] = categories.get(ftype, 0) + 1
        return categories

    def find_root_cause(self, failure: EvalResult) -> str:
        scores = {
            "faithfulness": failure.faithfulness,
            "relevance": failure.relevance,
            "completeness": failure.completeness
        }
        min_score = min(scores.values())
        lowest_metrics = [k for k, v in scores.items() if v == min_score]
        
        if len(lowest_metrics) > 1:
            return "Multiple issues detected — review full pipeline"
        
        lowest = lowest_metrics[0]
        if lowest == "faithfulness":
            return "Context is missing or irrelevant — improve retrieval"
        elif lowest == "relevance":
            return "Answer does not address the question — improve prompt clarity"
        else:
            return "Answer is missing key information — increase context window or improve generation"

    def generate_improvement_log(self, failures: list[EvalResult], suggestions: list[str]) -> str:
        rows = [
            "| Failure ID | Type | Root Cause | Suggested Fix | Status |",
            "|------------|------|------------|---------------|--------|"
        ]
        for i, failure in enumerate(failures):
            fid = f"F{i+1:03d}"
            ftype = failure.failure_type or "unknown"
            root_cause = self.find_root_cause(failure)
            suggested_fix = suggestions[i] if i < len(suggestions) else "Investigate pipeline parameters"
            rows.append(f"| {fid} | {ftype} | {root_cause} | {suggested_fix} | Open |")
        return "\n".join(rows)

    def generate_improvement_suggestions(
        self, failures: list[EvalResult]
    ) -> list[str]:
        categories = self.categorize_failures(failures)
        suggestions = []
        
        if categories.get("hallucination", 0) > 0:
            suggestions.append("Implement hallucination checker to filter unsupported claims")
        if categories.get("incomplete", 0) > 0:
            suggestions.append("Increase chunk size in RAG pipeline to reduce context fragmentation")
            suggestions.append("Add few-shot examples showing complete answers to improve completeness")
        if categories.get("irrelevant", 0) > 0 or categories.get("off_topic", 0) > 0:
            suggestions.append("Refine prompt instructions and add clear system guidelines to focus on the question")
        
        default_suggestions = [
            "Implement hallucination checker to filter unsupported claims",
            "Increase chunk size in RAG pipeline to reduce context fragmentation",
            "Add few-shot examples showing complete answers to improve completeness",
            "Refine prompt instructions and add clear system guidelines to focus on the question"
        ]
        for ds in default_suggestions:
            if len(suggestions) >= 3:
                break
            if ds not in suggestions:
                suggestions.append(ds)
                
        return suggestions


if __name__ == "__main__":
    # Test script will run if needed
    pass
