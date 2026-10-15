# Mapping of tracker concepts to OCEL elements

Step 1 of object-centric project mining maps the records of an issue tracker
to an object-centric event log (OCEL 2.0). Only this step depends on the
tracker; Steps 2–5 operate on any OCEL.

| Concept | OCEL element | Jira | GitHub | Redmine |
|---|---|---|---|---|
| Issue | object (type `issue`) | Issue | Issue | Issue |
| Epic | object (type `epic`) | Epic | Parent issue / tracking issue | Parent task |
| Release | object (type `release`) | Fix version | Milestone | Target version |
| Sprint | object (type `sprint`) | Sprint | Iteration field (Projects) | — (no sprints in core Redmine) |
| Component | object (type `component`) | Component | Label | Category |
| User | object (type `user`) | Assignee, reporter, comment author | Assignee, author | Assignee, author |
| Dependency link | O2O relation (qualifier = link type) **and** events *link added* / *link removed* | Issue link | Issue dependency (*blocked by* / *blocking*) | Issue relation (*blocks*, *precedes*, ...) |
| Hierarchy | O2O relation (`part of`) **and** events | Sub-task, epic link | Sub-issue | Parent task |
| Membership in release, sprint, component | O2O relation **and** events | Fix version, Sprint, Component fields | Milestone, Iteration, Label | Target version, Category |
| Field change | event related to the issue and all objects the change involves | Change log | Timeline event | Journal (journal details) |
| Text | attribute value (`val`) | Description, comment | Body, comment | Description, note |

## Notes

- **Relation history.** OCEL 2.0 object-to-object relations carry no
  timestamps. Every addition or removal of a relation is therefore also
  recorded as an event related to both objects; the period in which a relation
  held is reconstructed from these events.
- **Events with several objects.** Adding a link involves both issues; moving
  an issue to another release involves the issue and both releases. Jira's
  change log records a link change on both issues, i.e., duplicated, as a
  case-centric log would; the OCEL stores it once.
- **Status categories.** Statuses are mapped to the categories *to do*,
  *in progress*, and *done*. The start of a task is the first transition into
  an *in-progress* status. The per-project status sets used in the evaluation
  are listed in `code/start_signals.py` (`ACTIVE`).

## Jira field names used (TAWOS change log)

| Concept | `Change_Log.Field` |
|---|---|
| Status transition | `status` |
| Resolution | `resolution` |
| Dependency link | `Link` |
| Hierarchy | `Parent`, `Parent Issue`, `Epic Link`, `Epic Child` |
| Release | `Fix Version` |
| Sprint | `Sprint` |
| Component | `Component` |
| Assignee | `assignee` |
| Story points | `Story Points` |
