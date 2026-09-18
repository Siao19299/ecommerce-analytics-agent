-- case_id: D14_RU_009
WITH RECURSIVE counter(x) AS (
    SELECT 1
    UNION ALL
    SELECT x + 1 FROM counter WHERE x < :max_counter
)
SELECT x
FROM counter
ORDER BY x DESC
LIMIT 1;
