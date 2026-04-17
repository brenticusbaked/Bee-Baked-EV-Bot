from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Iterable, List, Tuple

from services.tasks import (
    PipelineTask,
    get_audit_tasks,
    get_parallel_tasks,
    get_refresh_tasks,
    get_scan_tasks,
)


TaskResult = Tuple[str, bool, str]


def run_task(task: PipelineTask) -> TaskResult:
    print(f"[start] {task.name}")
    try:
        task.func()
        return (task.name, True, "finished")
    except Exception as exc:
        return (task.name, False, str(exc))


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
                results.append((task.name, False, str(exc)))
    return results


def print_results(results: Iterable[TaskResult]) -> None:
    for name, ok, detail in results:
        status = "ok" if ok else "failed"
        print(f"[{status}] {name}: {detail}")


def run_master_pipeline() -> None:
    print(f"BEE-BAKED PIPELINE STARTING - {datetime.now().isoformat()}")

    print("--- PHASE 1: REFRESHING CLOUD CACHE ---")
    print_results(run_sequential(get_refresh_tasks()))

    print("--- PHASE 2: EXECUTING MODELS & SCRAPERS ---")
    print_results(run_parallel(get_parallel_tasks()))

    print("--- PHASE 3: UNIFIED MARKET SCAN ---")
    print_results(run_sequential(get_scan_tasks()))

    print("--- PHASE 4: POST-GAME AUDIT ---")
    print_results(run_sequential(get_audit_tasks()))

    print("BEE-BAKED PIPELINE COMPLETE.")
