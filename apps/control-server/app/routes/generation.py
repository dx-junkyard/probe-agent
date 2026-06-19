import ast
import json
import subprocess
import sys
import textwrap
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_system_id
from ..db import get_conn
from ..llm import LLMError, get_llm_client
from ..models import GenerationRun, GenerationRunCreate

router = APIRouter()

_VERDICTS = {"better", "worse", "same", "unsafe", "error", "unknown"}


def _json_or_empty(raw: Optional[str]) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _parse_repr(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def _trace_call_args(input_value: Any) -> tuple[list[Any], dict[str, Any]]:
    if not isinstance(input_value, dict):
        return [input_value], {}
    args = [_parse_repr(arg) for arg in input_value.get("args", [])]
    kwargs = {
        str(key): _parse_repr(value)
        for key, value in (input_value.get("kwargs") or {}).items()
    }
    return args, kwargs


def _extract_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < start:
            raise HTTPException(502, "LLM response did not contain JSON")
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise HTTPException(502, "LLM response JSON must be an object")
    return value


def _row_to_generation_run(row) -> GenerationRun:
    return GenerationRun(
        id=row["id"],
        system_id=row["system_id"],
        component_id=row["component_id"],
        trace_id=row["trace_id"],
        objective=row["objective"],
        input_json=_json_or_empty(row["input_json"]),
        current_output=row["current_output"],
        generated_code=row["generated_code"] or "",
        generation_notes=row["generation_notes"] or "",
        candidate_output=row["candidate_output"],
        execution_error=row["execution_error"],
        llm_verdict=row["llm_verdict"] or "unknown",
        llm_reason=row["llm_reason"] or "",
        llm_risks=row["llm_risks"] or "",
        llm_recommendation=row["llm_recommendation"] or "",
        created_at=row["created_at"],
    )


def _fetch_context(conn, system_id: int, component_id: str, trace_id: str) -> Dict[str, Any]:
    trace = conn.execute(
        """
        SELECT trace_id, component_id, input_json, output_text, error
        FROM traces
        WHERE system_id = ? AND component_id = ? AND trace_id = ?
        """,
        (system_id, component_id, trace_id),
    ).fetchone()
    if trace is None:
        raise HTTPException(404, "trace not found")
    system_profile = conn.execute(
        "SELECT * FROM system_profile WHERE system_id = ?", (system_id,)
    ).fetchone()
    component_profile = conn.execute(
        """
        SELECT * FROM component_profiles
        WHERE system_id = ? AND component_id = ?
        """,
        (system_id, component_id),
    ).fetchone()
    criteria = conn.execute(
        """
        SELECT name, description, criterion_type, expected_value, weight
        FROM evaluation_criteria
        WHERE system_id = ? AND component_id = ? AND enabled = 1
        ORDER BY id
        """,
        (system_id, component_id),
    ).fetchall()
    return {
        "trace": dict(trace),
        "system_profile": dict(system_profile) if system_profile else {},
        "component_profile": dict(component_profile) if component_profile else {},
        "criteria": [dict(row) for row in criteria],
    }


def _build_generation_prompt(context: Dict[str, Any], objective: str) -> List[Dict[str, str]]:
    trace = context["trace"]
    payload = {
        "objective": objective,
        "component_id": trace["component_id"],
        "trace_input": _json_or_empty(trace["input_json"]),
        "current_output": trace["output_text"],
        "current_error": trace["error"],
        "system_profile": context["system_profile"],
        "component_profile": context["component_profile"],
        "evaluation_criteria": context["criteria"],
    }
    return [
        {
            "role": "system",
            "content": (
                "You generate small Python candidate functions for probe-agent. "
                "Return only JSON. The generated code must define "
                "`candidate(*args, **kwargs)`. Do not use imports, file I/O, "
                "network calls, subprocesses, or environment access."
            ),
        },
        {
            "role": "user",
            "content": (
                "Generate a candidate implementation for this observed trace.\n"
                "Response schema: {\"generated_code\": string, \"notes\": string}.\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
            ),
        },
    ]


def _build_evaluation_prompt(
    context: Dict[str, Any],
    objective: str,
    generated_code: str,
    candidate_output: Optional[str],
    execution_error: Optional[str],
) -> List[Dict[str, str]]:
    trace = context["trace"]
    payload = {
        "objective": objective,
        "component_id": trace["component_id"],
        "trace_input": _json_or_empty(trace["input_json"]),
        "current_output": trace["output_text"],
        "candidate_output": candidate_output,
        "execution_error": execution_error,
        "generated_code": generated_code,
        "system_profile": context["system_profile"],
        "component_profile": context["component_profile"],
        "evaluation_criteria": context["criteria"],
    }
    return [
        {
            "role": "system",
            "content": (
                "You are an evaluation LLM for generated candidate code. "
                "Judge whether the candidate better satisfies the objective and "
                "context than the current output. Return only JSON."
            ),
        },
        {
            "role": "user",
            "content": (
                "EVALUATION_RESPONSE_JSON schema: "
                "{\"verdict\":\"better|worse|same|unsafe|error|unknown\","
                "\"reason\":string,\"risks\":string,\"recommendation\":string}.\n"
                + json.dumps(payload, ensure_ascii=False, indent=2)
            ),
        },
    ]


def _run_candidate(code: str, input_value: Any) -> tuple[Optional[str], Optional[str]]:
    args, kwargs = _trace_call_args(input_value)
    runner = textwrap.dedent(
        """
        import json
        import traceback

        SAFE_BUILTINS = {
            "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
            "enumerate": enumerate, "float": float, "int": int, "len": len,
            "list": list, "max": max, "min": min, "range": range, "repr": repr,
            "round": round, "set": set, "sorted": sorted, "str": str, "sum": sum,
            "tuple": tuple, "zip": zip,
        }

        payload = json.loads(input())
        namespace = {"__builtins__": SAFE_BUILTINS}
        result = {"output": None, "error": None}
        try:
            exec(payload["code"], namespace, namespace)
            candidate = namespace.get("candidate")
            if not callable(candidate):
                raise RuntimeError("generated code must define callable candidate")
            output = candidate(*payload["args"], **payload["kwargs"])
            result["output"] = repr(output)
        except BaseException:
            result["error"] = traceback.format_exc(limit=8)
        print(json.dumps(result, ensure_ascii=False))
        """
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-S", "-c", runner],
            input=json.dumps(
                {"code": code, "args": args, "kwargs": kwargs}, ensure_ascii=False
            ),
            text=True,
            capture_output=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None, "candidate execution timed out"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or "candidate runner failed")[:4000]
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, f"candidate runner returned invalid JSON: {proc.stdout[:1000]}"
    return result.get("output"), result.get("error")


@router.post("/generation-runs", response_model=GenerationRun, status_code=201)
def create_generation_run(
    payload: GenerationRunCreate,
    system_id: int = Depends(get_system_id),
) -> GenerationRun:
    with get_conn() as conn:
        context = _fetch_context(conn, system_id, payload.component_id, payload.trace_id)

    try:
        raw_code = get_llm_client().generate_text(
            _build_generation_prompt(context, payload.objective),
            temperature=0.2,
            max_tokens=1800,
        )
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    generation = _extract_json_object(raw_code)
    generated_code = str(generation.get("generated_code") or "").strip()
    if "def candidate" not in generated_code:
        raise HTTPException(502, "LLM response did not define candidate function")
    generation_notes = str(generation.get("notes") or "")

    trace_input = _json_or_empty(context["trace"]["input_json"])
    candidate_output, execution_error = _run_candidate(generated_code, trace_input)

    try:
        raw_eval = get_llm_client().generate_text(
            _build_evaluation_prompt(
                context,
                payload.objective,
                generated_code,
                candidate_output,
                execution_error,
            ),
            temperature=0,
            max_tokens=1000,
        )
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    evaluation = _extract_json_object(raw_eval)
    verdict = str(evaluation.get("verdict") or "unknown").lower()
    if verdict not in _VERDICTS:
        verdict = "unknown"

    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO generation_runs
                (system_id, component_id, trace_id, objective, input_json,
                 current_output, generated_code, generation_notes,
                 candidate_output, execution_error, llm_verdict, llm_reason,
                 llm_risks, llm_recommendation, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                payload.component_id,
                payload.trace_id,
                payload.objective,
                json.dumps(trace_input, ensure_ascii=False)
                if trace_input is not None
                else None,
                context["trace"]["output_text"],
                generated_code,
                generation_notes,
                candidate_output,
                execution_error,
                verdict,
                str(evaluation.get("reason") or ""),
                str(evaluation.get("risks") or ""),
                str(evaluation.get("recommendation") or ""),
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM generation_runs WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return _row_to_generation_run(row)


@router.get("/generation-runs", response_model=List[GenerationRun])
def list_generation_runs(
    component_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    limit: int = 20,
    system_id: int = Depends(get_system_id),
) -> List[GenerationRun]:
    limit = max(1, min(limit, 100))
    where = ["system_id = ?"]
    params: List[Any] = [system_id]
    if component_id:
        where.append("component_id = ?")
        params.append(component_id)
    if trace_id:
        where.append("trace_id = ?")
        params.append(trace_id)
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM generation_runs
            WHERE {' AND '.join(where)}
            ORDER BY id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_row_to_generation_run(row) for row in rows]


@router.get("/generation-runs/{run_id}", response_model=GenerationRun)
def get_generation_run(
    run_id: int,
    system_id: int = Depends(get_system_id),
) -> GenerationRun:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM generation_runs WHERE id = ? AND system_id = ?",
            (run_id, system_id),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "generation run not found")
    return _row_to_generation_run(row)
