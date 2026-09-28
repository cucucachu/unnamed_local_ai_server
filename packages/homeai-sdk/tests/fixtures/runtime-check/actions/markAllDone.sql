UPDATE items SET done = 1 WHERE done = 0;
SELECT count(*) AS n FROM items WHERE done = 1;
