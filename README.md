# Equipment Microservice

Реестр техники организации, выдача техники рабочим (аренда) и учёт её расходов
по дням: топливо, амортизация, накладные. Расходы считает внешняя система;
сервис передаёт ей заявки, запрашивает расчёты и хранит результат. Пока внешней
системы нет, её заменяет заглушка `cost-system-stub` из этого же репозитория.

## Архитектура

```text
Gateway REST → gRPC EquipmentService / RentalService
  → application (EquipmentService, RentalService) → PostgreSQL (схема equipment)
                                                   └→ outbox (в той же транзакции)
SubmissionService (воркер)  outbox → POST внешней системы (повторы, backoff)
AccrualService (воркер)     завершённые сутки → POST /v1/cost-calculations → начисления
```

- `src/domain/equipment` — модели с инвариантами и расчёт суточных периодов (`periods.py`).
- `src/application/equipment` — сценарии и порты (`Protocol`).
- `src/infrastructure` — PostgreSQL-репозитории, HTTP-клиент внешней системы, воркеры.
- `src/entrypoints/gRPC` — gRPC API; контракт `src/domains/equipment/equipment.proto`
  (submodule `grpc_contracts_vkr`).
- `src/cost_system_stub` — заглушка внешней системы (`python -m src.cost_system_stub`).

## Правила

- Техника: инвентарный номер уникален в организации; статусы `available`,
  `maintenance`, `decommissioned`. Арендовать можно только `available`.
- Аренда (`open` → `returned` | `cancelled`): пересечение с открытой арендой той же
  техники запрещено. Открытая аренда держит технику до возврата, даже после плановой
  даты. Проверка идёт в транзакции с блокировкой строки техники.
- Отменить можно только до начала, вернуть — не раньше начала и не раньше конца уже
  начисленных суток.
- Начисления — по суткам UTC. Сутки открытой аренды начисляются после полуночи,
  возвращённой — до момента возврата (первый и последний день бывают неполными).
  Одни сутки аренды начисляются один раз (`UNIQUE (rental_id, accrual_date)`).
- Начисления идут только после того, как внешняя система приняла заявку
  (`externalStatus = submitted`). Когда все сутки возвращённой аренды начислены,
  расходы помечаются `complete`.
- Все запросы ограничены `organizationId`; чужой ресурс — `NOT_FOUND`.

## Внешняя система (HTTP API)

Сервис вызывает её сам (`COST_SYSTEM_URL`, необязательный `COST_SYSTEM_TOKEN` →
`Authorization: Bearer`). 5xx, 408, 425, 429, обрыв и таймаут — повтор позже;
остальные 4xx и некорректный ответ — отказ.

| Запрос | Тело | Ответ |
| --- | --- | --- |
| `POST /v1/rentals` | `rentalId`, `organizationId`, `workerUserId`, `startsAt`, `endsAt`, `shiftHoursPerDay`, `purpose`, `equipment{id, inventoryNumber, name, catalogItemId}` | `{"reference": "..."}` |
| `POST /v1/rentals/{rentalId}/return` | то же + `returnedAt` | любой JSON |
| `POST /v1/rentals/{rentalId}/cancel` | то же | любой JSON |
| `POST /v1/cost-calculations` | `rentalId`, `externalReference`, `accrualDate`, `periodStart`, `periodEnd`, `shiftHoursPerDay`, `equipment{purchasePrice, residualValue, usefulLifeMonths, fuelType, fuelConsumptionPerHour, dailyOverhead, currency, ...}` | `{"calculationId", "currency", "lines": [{"costType", "amount", "quantity"?, "unit"?, "description"?}]}` |

Расчёт идёт с заголовком `Idempotency-Key: <rentalId>:<accrualDate>`. Деньги — строки.

Заявки уходят через outbox: при сбое повторы через 5 с, 10 с, 20 с… (не больше
часа, `SUBMIT_MAX_ATTEMPTS`), сообщения одной аренды — строго по порядку. Если
регистрация отклонена, у аренды `externalStatus = failed`.

### Заглушка

Формулы (`src/cost_system_stub/calculator.py`):
- топливо = моточасы × расход в час × цена (`STUB_FUEL_PRICES`); моточасы — смена
  `shiftHoursPerDay`, пропорционально длине периода;
- амортизация = (стоимость − остаточная) / срок в месяцах / 30.4375 × доля суток;
- накладные = `dailyOverhead` × доля суток.

`STUB_FAILURE_RATE` (0–1) — доля ответов 503 для проверки повторов. Каждый запрос
заглушка пишет в лог.

## Запуск и проверка

В корневом compose: `equipment-service` (gRPC 50055), `equipment-db`,
`cost-system-stub` (8090). Нужен `EQUIPMENT_DB_PASSWORD` в `.env`.

```bash
uv sync --frozen
.venv/bin/python -m pytest -q    # интеграционные тесты — с PostgreSQL из tests/common.py
.venv/bin/mypy src tests
```

Интеграционные тесты (`-m integration`) ждут PostgreSQL на `127.0.0.1:55432`
(`DB_*` в `tests/common.py`) и пропускаются, если его нет:

```bash
docker run --rm -d --name equipment-test-db -p 127.0.0.1:55432:5432 \
  -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=equipment_test postgres:17-alpine
```
