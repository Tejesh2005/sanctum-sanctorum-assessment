# Sanctum Sanctorum — Submission Notes

**Live application:** https://sanctum-sanctorum-assessment-production.up.railway.app

The frontend is available at `/`, the OpenAPI interface at `/docs`, and the health check at
`/health`. The production database starts with the supplied demo data. Useful member IDs are:

| ID | Member | Tier |
| --- | --- | --- |
| 1 | Wong Li | supreme |
| 2 | Christine Palmer | master |
| 3 | Jonathan Pangborn | adept |
| 4 | Sara Lin | apprentice |

## What I completed

- Book creation, ISBN-13 normalization/checksum validation, duplicate protection, partial updates,
  filtering, sorting and pagination.
- Member creation, normalization, duplicate protection, tier-based access rules and activity stats.
- Order validation, price snapshots, tier/bulk discounts, atomic stock reservation, payment and
  cancellation with stock restoration.
- Loan creation, tier limits, restricted-book access, overdue blocking, returns, status filtering
  and capped late-fee calculation.
- Paid-order top-books reporting.
- The supplied frontend is served by FastAPI and supports catalogue, member, order, loan and report
  workflows. I also fixed a missing event-dispatch case for the loan Return button found during
  manual browser testing.
- All optional extras:
  - `GET /members` with deterministic offset pagination, demonstrated in the Members tab with page-size,
    Previous and Next controls.
  - Concurrency-safe ordering for the last available copy.
  - Additional regression tests for literal wildcard searches, ignored PATCH fields, return-time
    late-fee caps, member pagination and simultaneous final-copy orders.

Nothing from the required scope is intentionally left incomplete. The full local suite passes
**216 tests**.

## Architecture and design decisions

### Layering

Routers remain responsible for HTTP parsing, dependency injection and response contracts. Domain
rules live in service modules, validation and normalization live in Pydantic schemas, and persistence
is represented by SQLAlchemy models. This keeps the business rules callable and testable without
coupling them to FastAPI request objects.

### Transactional stock reservation

An order reserves inventory when it is created. All requested books are validated before mutation,
and every decrement and the new order are committed in one transaction. A failure rolls the whole
transaction back.

For the optional final-copy race, stock is decremented with a conditional database update
(`stock >= requested quantity`). Databases with row-level locking load books with `FOR UPDATE` in a
stable ID order. SQLite does not implement row-level `FOR UPDATE`, so it starts the short order
transaction with `BEGIN IMMEDIATE`; a competing writer waits and then observes the committed stock.
A two-connection regression test verifies that two simultaneous requests for stock `1` result in
one order and one `409`, never two orders.

### Time and money

All time-dependent rules use the supplied `get_now` dependency, which keeps due dates and tests
deterministic. Money remains integer cents throughout the application, and percentage discounts use
integer flooring as specified.

### Member listing

The optional `GET /members` contract was not specified in detail. I mirrored the existing book-page
shape: `{items, total, limit, offset}`, with default `limit=20`, bounds `1..100`, non-negative offset
and stable ID ordering. The frontend Member directory consumes this endpoint directly, shows the
current range and total, and disables Previous or Next when the user reaches the corresponding edge.

### Interface and authorization boundary

I treat the web interface as an internal staff-facing demonstration tool. Selecting a member sets
the customer context for orders and loans; it is not secure member authentication. This explains why
the same interface also exposes book and inventory management. The assignment defines neither
authentication nor staff/member authorization, and the book endpoints receive no caller identity,
so I did not invent access rules outside the contract. A production version would authenticate staff
and members and apply role-based authorization to inventory, reporting and account data.

## Deployment

The application is deployed as one Railway service: FastAPI serves both the API and the static
frontend. The service runs with:

```text
uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

The existing SQLite database is stored on a Railway persistent volume mounted at `/data`, with:

```text
SANCTUM_DATABASE_URL=sqlite:////data/sanctum.db
```

This choice keeps the supplied stack and the no-new-dependency constraint while avoiding ephemeral
filesystem data loss. Persistence was verified by creating and returning a loan, redeploying the
service, and confirming the same returned-loan record remained. SQLite plus one attached volume is a
good fit for this small single-replica assessment. For production scale-out, I would migrate to
PostgreSQL and introduce schema migrations rather than share a SQLite file between replicas.

Railway's automatic Railpack redeployment did not detect the config-file start command reliably, so
the same command is also configured explicitly in the service settings. `/health` is used as the
deployment health check.

## Ambiguities and trade-offs

- `SPEC.md` says not to add dependencies, while the deployment guide encourages considering hosted
  PostgreSQL, which requires a PostgreSQL driver not present in the project. I preserved SQLite and
  used a persistent volume so local and deployed behaviour remain aligned without another dependency.
- The optional member-list endpoint had no specified page schema or bounds. I deliberately matched
  the established book pagination contract rather than introducing a second pagination style.
- Authentication and separate administrator/member roles are outside the specification. I documented
  the staff-facing interpretation instead of adding unrequested security behaviour that would change
  the API contract.

## What I would do with more time

- Add staff/member authentication and role-based authorization.
- Move production data to PostgreSQL with Alembic migrations for multi-replica deployments.
- Add CI that runs the full suite for every pull request.
- Add structured logging, request tracing and deployment monitoring.
- Add integration tests for the browser interface in addition to API tests.

## AI usage

I used ChatGPT/Codex to help interpret the specification, break the work into incremental changes,
draft implementation and test ideas, investigate failures, review edge cases and guide the Railway
deployment. I reviewed the generated changes, ran focused and full tests, inspected diffs, and
manually exercised the local and deployed interfaces.

One important AI miss was the frontend Return button: the backend return implementation and tests
were correct, but the generated UI action dispatcher omitted the `loan-return` case. Manual testing
showed that clicking the button sent no request. I traced the browser/server behaviour, added the
missing dispatch case and reran the full suite. This reinforced that passing backend tests is not a
substitute for exercising the actual interface.
