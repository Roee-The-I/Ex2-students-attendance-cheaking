# Graph Report - Ex2-students-attendance-cheaking-main  (2026-11-23)

## Corpus Check
- 9 files · ~5,197 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 4 file(s) not represented in the graph (top: (none) 3, .csv 1)

## Summary
- 66 nodes · 148 edges · 12 communities (3 shown, 9 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 2 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `39e466f3`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- routes.py
- __init__.py
- security.py
- EX2.py
- get_qr_code
- config.py
- AttendanceReviewTests
- close_db
- database.py
- route

## God Nodes (most connected - your core abstractions)
1. `AttendanceReviewTests` - 9 edges
2. `generate_token()` - 8 edges
3. `get_db()` - 7 edges
4. `get_qr_code()` - 7 edges
5. `submit_attendance()` - 6 edges
6. `lecturer_required()` - 6 edges
7. `create_app()` - 6 edges
8. `get_qr_code()` - 6 edges
9. `init_db()` - 6 edges
10. `classroom_location()` - 5 edges

## Surprising Connections (you probably didn't know these)
- `submit_attendance()` --calls--> `is_token_valid()`  [EXTRACTED]
  attendance_app/routes.py → attendance_app/security.py
- `get_qr_code()` --references--> `lecturer_required()`  [EXTRACTED]
  attendance_app/routes.py → attendance_app/security.py
- `create_app()` --indirect_call--> `close_db()`  [INFERRED]
  attendance_app/__init__.py → attendance_app/database.py
- `create_app()` --calls--> `init_db()`  [EXTRACTED]
  attendance_app/__init__.py → attendance_app/database.py
- `get_qr_code()` --calls--> `generate_token()`  [EXTRACTED]
  attendance_app/routes.py → attendance_app/security.py

## Import Cycles
- None detected.

## Communities (12 total, 9 thin omitted)

### Community 0 - "routes.py"
Cohesion: 0.36
Nodes (8): get_db(), attendance_list(), classroom_location(), get_classroom_location(), index(), lecturer(), submit_attendance(), lecturer_required()

### Community 2 - "security.py"
Cohesion: 0.43
Nodes (3): generate_token(), is_token_valid(), generate_token()

### Community 3 - "EX2.py"
Cohesion: 0.36
Nodes (4): attendance_list(), get_db(), is_token_valid(), submit_attendance()

## Knowledge Gaps
- **9 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AttendanceReviewTests` connect `AttendanceReviewTests` to `test_attendance_review.py`?**
  _High betweenness centrality (0.178) - this node is a cross-community bridge._
- **Why does `generate_token()` connect `security.py` to `routes.py`, `test_attendance_review.py`, `get_qr_code`, `AttendanceReviewTests`?**
  _High betweenness centrality (0.081) - this node is a cross-community bridge._
- **Why does `lecturer_required()` connect `routes.py` to `security.py`, `get_qr_code`?**
  _High betweenness centrality (0.035) - this node is a cross-community bridge._