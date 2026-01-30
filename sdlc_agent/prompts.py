from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent

CODE_AGENT_SYSTEM = dedent(
    """    Ты автономный **Code Agent** в GitHub SDLC пайплайне.

    Твоя задача: по описанию Issue предложить изменения кода в репозитории.
    Ты НЕ запускаешь код и НЕ видишь CI напрямую — поэтому делай изменения консервативно и минимально.

    Ключевые правила:
    - Меняй только то, что нужно для выполнения требований.
    - Сохраняй существующий стиль/архитектуру.
    - Не выдумывай файлы: используй только пути из списка.
    - Если нужен новый файл — добавь его через unified diff.
    - Выходной формат должен быть строго соблюдён (см. ниже).
    """
).strip()


REVIEWER_SYSTEM = dedent(
    """    Ты автономный **AI Reviewer Agent** в GitHub Actions.

    Твоя цель: проверить PR относительно Issue и результатов CI.
    Учитывай:
    - diff (реальные изменения)
    - результаты CI команд (успех/провал + логи)
    - требования Issue (что просили сделать)

    Правила:
    - Никаких галлюцинаций: утверждай только то, что видно в diff/логах/Issue.
    - Если CI упал — почти всегда требуется правка (needs_changes=true).
    - Будь практичным: предложи конкретные шаги исправления.
    - Выдай результат строго JSON.
    """
).strip()


@dataclass(frozen=True)
class IssueContext:
    number: int
    title: str
    body: str


def build_file_select_prompt(issue: IssueContext, all_files: list[str]) -> str:
    file_list = "\n".join(f"- {p}" for p in all_files)
    return dedent(
        f"""        Issue #{issue.number}: {issue.title}

        Описание:
        {issue.body}

        Ниже список файлов репозитория. Выбери МАКСИМУМ 8 файлов, которые нужно прочитать, чтобы решить задачу.
        Если задача простая и очевидная — всё равно выбери 1-3 наиболее релевантных файла.

        Верни СТРОГО JSON (без markdown), формат:
        {{
          "files": ["path1", "path2", "..."],
          "reason": "коротко почему эти файлы"
        }}

        Список файлов:
        {file_list}
        """
    ).strip()


def build_patch_prompt(issue: IssueContext, files_with_content: dict[str, str], feedback: str | None) -> str:
    parts: list[str] = []
    for path, content in files_with_content.items():
        parts.append(f"--- FILE: {path} ---\n{content}\n--- END FILE: {path} ---\n")
    files_blob = "\n".join(parts)

    fb = "" if not feedback else f"\n\nДоп. замечания от ревьюера (учти их!):\n{feedback}\n"

    return dedent(
        f"""        Issue #{issue.number}: {issue.title}

        Описание:
        {issue.body}
        {fb}

        Ниже — содержимое выбранных файлов.
        Сгенерируй ПАТЧ в формате unified diff (git), чтобы решить задачу.

        Ограничения:
        - Меняй минимум файлов.
        - Не трогай .github/workflows, если Issue явно не про это.
        - Патч должен применяться командой `git apply`.
        - В diff должны быть строки вида `diff --git a/... b/...`.

        Ответ:
        - Сначала короткий план (до 5 буллетов).
        - Затем ОДИН блок кода ```diff ...```.

        Файлы:
        {files_blob}
        """
    ).strip()


def build_review_prompt(
    *,
    issue: IssueContext,
    pr_title: str,
    pr_body: str,
    diff: str,
    ci_summary: str,
    ci_logs_tail: str,
) -> str:
    return dedent(
        f"""        Issue #{issue.number}: {issue.title}

        Issue body:
        {issue.body}

        PR title: {pr_title}

        PR body:
        {pr_body}

        CI summary:
        {ci_summary}

        CI logs (tail):
        {ci_logs_tail}

        PR diff:
        {diff}

        Верни СТРОГО JSON (без markdown), формат:
        {{
          "needs_changes": true/false,
          "summary_md": "короткий итог (1-3 предложения)",
          "review_md": "подробный review в markdown (чеклист, замечания, рекомендации)",
          "action_items": ["...", "..."],
          "confidence": 0.0-1.0
        }}

        Подсказка:
        - needs_changes=true, если CI не зелёный ИЛИ требования Issue не выполнены.
        - Если уверенность низкая, укажи это в confidence и проси минимальные уточнения в action_items.
        """
    ).strip()
