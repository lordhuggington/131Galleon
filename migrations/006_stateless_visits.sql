-- Visits no longer happen on fixed days: the housekeeper fills the run sheet in on whatever day they work.
-- Tasks are no longer pinned to Tuesday or Friday. The day column and its CHECK stay (SQLite can't
-- drop a CHECK without rebuilding the table, and 'any' satisfies it); the app always writes 'any'.
UPDATE tasks SET day = 'any' WHERE day <> 'any';
