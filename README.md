# SDLC Agent (Code Agent + AI Reviewer) for GitHub

Этот репозиторий — шаблон для проекта из трека **Coding Agents**: агентная система, которая делает полный цикл SDLC в GitHub:

1. Issue → **Code Agent** создаёт/обновляет PR с кодом.
2. PR → CI запускает линтеры/тесты + **Reviewer Agent**.
3. Если Reviewer просит правки → PR помечается лейблом `agent:fix`, и Code Agent делает следующий коммит в тот же PR.
4. Цикл повторяется до успеха или лимита итераций.

> Важно: чтобы PR/Push от агента **триггерили другие workflow**, обычно нужен **PAT** или токен GitHub App (у `GITHUB_TOKEN` есть ограничения на триггеринг workflow).

---

## Быстрый старт (локально)

1) Скопируйте `.env.example` → `.env` и заполните ключи.
2) Поднимите контейнер:

```bash
docker compose up -d --build
```

3) Запустите агента внутри контейнера:

```bash
docker compose exec agent code-agent issue --repo owner/repo --issue 123
```

---

## Быстрый старт (в GitHub Actions)

Смотрите `.github/workflows/`:

- `code_agent.yml` — реагирует на Issue и на лейбл `agent:fix` на PR
- `pr_ci_review.yml` — CI + Reviewer Agent на PR

Нужно добавить secrets в репозиторий:

- `AGENT_GITHUB_TOKEN` — PAT (или GitHub App token) с правами на push/PR
- `OPENAI_API_KEY` — ключ для LLM (если используете OpenAI)

---

## Переменные окружения

- `AGENT_GITHUB_TOKEN` — токен GitHub (PAT или GitHub App)
- `OPENAI_API_KEY` — ключ OpenAI
- `OPENAI_MODEL` — модель, по умолчанию `gpt-4o-mini`
- `AGENT_MAX_ITERS` — максимум итераций (по умолчанию 3)
- `AGENT_BASE_BRANCH` — базовая ветка (если нужно переопределить)

---

## Как это устроено

- **State machine** делается через GitHub labels:
  - `agent:managed` — PR под управлением агента
  - `agent:fix` — требуется следующий цикл исправлений
  - `agent:done` — всё ок
  - `agent:iter-<N>` — номер итерации

- **Code Agent** генерирует изменения в формате unified diff и применяет их через `git apply`.

- **Reviewer Agent** анализирует:
  - diff в PR
  - результаты CI (код-стайл, типы, тесты)
  - требования Issue (PR body содержит `Closes #<issue>`)

---

## Лицензия

MIT
