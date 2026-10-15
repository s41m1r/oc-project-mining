-- Slim copy of Change_Log with the fields the pipeline needs (runs in seconds).
-- Usage: sqlite3 ../data/tawos.sqlite < build_events.sql
DROP TABLE IF EXISTS Event;
CREATE TABLE Event AS
  SELECT ID, Issue_ID, Field, From_Value, To_Value, From_String, To_String, Creation_Date, Author_ID
  FROM Change_Log NOT INDEXED
  WHERE Field IN ('status','resolution','Link','Parent','Parent Issue','Fix Version','Sprint',
                  'assignee','Epic Link','Epic Child','Story Points','Component','issuetype');
CREATE INDEX ix_Event_issue ON Event (Issue_ID, Field);
CREATE INDEX ix_Event_field ON Event (Field, Issue_ID);
