import asyncio
import inspect
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from time import perf_counter
from typing import Dict, Iterable, List, Tuple

from db_manager import get_runtime_db_stats, log_workflow_run, reset_runtime_db_stats
from services.http_client import post_discord
from services.tasks import (
    PipelineTask,
    get_audit_tasks,
    get_parallel_tasks,
    get_refresh_tasks,
    get_scan_tasks,
    get_scraper_tasks,
)


TaskResult = Tuple[str, bool, str, float, int, str, Dict[str, str]]
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
DISCORD_STATUS_WEBHOOK_URL = os.getenv("DISCORD_STATUS_WEBHOOK_URL") or DISCORD_WEBHOOK_URL


def _normalize_task_output(output) -> Tuple[str, int, str, Dict[str, str]]:
    if isinstance(output, dict):
        detail = str(output.get("detail", "finished"))
        count = int(output.get("count", 0))
        label = str(output.get("label", "updates"))
        meta = output.get("meta", {})
        return detail, count, label, meta if isinstance(meta, dict) else {}
    if isinstance(output, int):
        return "finished", output, "updates", {}
    return "finished", 0, "updates", {}


def _run_callable(func):
    result = func()
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def run_task(task: PipelineTask) -> TaskResult:
    print(f"[start] {task.name}")
    started = perf_counter()
    try:
        output = _run_callable(task.func)
        detail, count, label, meta = _normalize_task_output(output)
        return (task.name, True, detail, perf_counter() - started, count, label, meta)
    except Exception as exc:
        return (task.name, False, str(exc), perf_counter() - started, 0, "updates", {})


def run_sequential(tasks: Iterable[PipelineTask]) -> List[TaskResult]:
    return [run_task(task) for task in tasks]


def run_parallel(tasks: Iterable[PipelineTask]) -> List[TaskResult]:
    task_list = list(tasks)
    if not task_list:
        return []

    results: List[TaskResult] = []
    with ThreadPoolExecutor(max_workers=len(task_list)) as executor:
        future_map = {executor.submit(run_task, task): task for task in task_list}
        for future in as_completed(future_map):
            task = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:
                results.append((task.name, False, str(exc), 0.0, 0, "updates", {}))
    return results


def print_results(results: Iterable[TaskResult]) -> None:
    for name, ok, detail, seconds, count, label, meta in results:
        status = "ok" if ok else "failed"
        count_text = f" | {count} {label}" if count else ""
        print(f"[{status}] {name}: {detail} ({seconds:.2f}s){count_text}")


def send_pipeline_summary(title: str, results: Iterable[TaskResult]) -> None:
    result_list = list(results)
    if not DISCORD_STATUS_WEBHOOK_URL:
        return

    total_seconds = sum(seconds for _, _, _, seconds, _, _, _ in result_list)
    total_updates = sum(count for _, _, _, _, count, label, _ in result_list if label == "updates")
    total_alerts = sum(count for _, _, _, _, count, label, _ in result_list if label == "alerts")
    total_graded = sum(count for _, _, _, _, count, label, _ in result_list if label == "graded")
    total_tracked = sum(count for _, _, _, _, count, label, _ in result_list if label == "tracked")
    db_stats = get_runtime_db_stats()
    failed = sum(1 for _, ok, _, _, _, _, _ in result_list if not ok)

    outcome_line = (
        "No bet updates found this run."
        if failed == 0 and total_alerts == 0
        else f"{total_alerts} bet update(s) sent this run."
        if failed == 0
        else "Run completed with failures. Review task details below."
    )

    task_lines = []
    near_miss_lines = []
    for name, ok, detail, seconds, count, label, meta in result_list:
        status = "OK" if ok else "FAILED"
        count_text = f" | {count} {label}" if count else ""
        detail_text = f" - {detail}" if detail else ""
        task_lines.append(f"`{status}` {name} ({seconds:.2f}s){count_text}{detail_text}")
        near_miss_summary = meta.get("near_miss_summary")
        if near_miss_summary:
            near_miss_lines.append(f"`{name}` {near_miss_summary}")

    description = (
        f"**{title}**\n"
        f"{outcome_line}\n\n"
        f"**Counts**\n"
        f"Tasks: {len(result_list)}\n"
        f"Failures: {failed}\n"
        f"Runtime: {total_seconds:.2f}s\n"
        f"Alerts: {total_alerts}\n"
        f"Updates: {total_updates}\n"
        f"CLV Tracked: {total_tracked}\n"
        f"Graded: {total_graded}\n"
        f"Bet Log Writes: {db_stats.get('bet_log_success', 0)} success / {db_stats.get('bet_log_failure', 0)} failed\n\n"
        f"**Task Timings**\n"
        + "\n".join(task_lines)
    )
    if near_miss_lines:
        description += "\n\n**Near Misses**\n" + "\n".join(near_miss_lines)
    if db_stats.get("bet_log_failure", 0) > 0:
        description += "\n\n**Warning:** Some bet alerts qualified but failed to write to `bets_log`."

    log_workflow_run(
        workflow_name=title,
        status="failed" if failed else "ok",
        runtime_seconds=total_seconds,
        task_count=len(result_list),
        failure_count=failed,
        alert_count=total_alerts,
        graded_count=total_graded,
        tracked_count=total_tracked,
        summary=description[:2000],
    )
    post_discord(
        {"embeds": [{"description": description, "color": 5763719 if failed == 0 else 15158332}]},
        webhook_url=DISCORD_STATUS_WEBHOOK_URL,
    )


def run_master_pipeline() -> None:
    print(f"BEE-BAKED PIPELINE STARTING - {datetime.now().isoformat()}")
    reset_runtime_db_stats()
    all_results: List[TaskResult] = []

    print("--- PHASE 1: REFRESHING CLOUD CACHE ---")
    results = run_sequential(get_refresh_tasks())
    all_results.extend(results)
    print_results(results)

    print("--- PHASE 2: EXECUTING MODELS & NEWS ---")
    results = run_parallel(get_parallel_tasks())
    all_results.extend(results)
    print_results(results)

    print("--- PHASE 3: UNIFIED MARKET SCAN ---")
    results = run_sequential(get_scan_tasks())
    all_results.extend(results)
    print_results(results)

    print("--- PHASE 4: POST-GAME AUDIT ---")
    results = run_sequential(get_audit_tasks())
    all_results.extend(results)
    print_results(results)

    send_pipeline_summary("BEE-BAKED CORE RUN COMPLETE", all_results)
    print("BEE-BAKED PIPELINE COMPLETE.")


def run_scraper_pipeline() -> None:
    print(f"BEE-BAKED SCRAPER PIPELINE STARTING - {datetime.now().isoformat()}")
    reset_runtime_db_stats()
    print("--- SCRAPER PHASE: EXECUTING BROWSER SCRAPERS ---")
    results = run_parallel(get_scraper_tasks())
    print_results(results)
    send_pipeline_summary("BEE-BAKED SCRAPER RUN COMPLETE", results)
    print("BEE-BAKED SCRAPER PIPELINE COMPLETE.")
