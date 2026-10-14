# DECISIONS.md

## 1. Indexes

I created a unique index on `employees.emp_code` to prevent duplicate employee codes. I created a unique compound index on `attendance_logs.emp_code` and `attendance_logs.date` to guarantee one attendance record per employee per attendance date. I also created an index on `attendance_logs.date` descending and `emp_code` ascending to support attendance listing in the required order. I considered adding more indexes for every analytics query, but avoided unnecessary indexes because they increase storage and write costs.

## 2. Punch-in race

If two identical punch-in requests arrive together, both may initially find no existing attendance record. Both then attempt insertion. MongoDB's unique compound index allows only one insertion to succeed. The other raises `DuplicateKeyError`, which the API converts to HTTP 409 Conflict.

## 3. Ties

The leaderboard must use standard competition ranking. If employees tie at the cutoff, all employees whose rank is within the limit are returned, including tied employees beyond that number of rows. Sorting by late minutes descending and employee code ascending makes the output deterministic.

## 4. Headcount

The department summary starts from employees rather than attendance logs. Therefore, employees who have joined by the end of the selected month are counted even when they have no attendance records.

## 5. Scaling

For 100 times more data, I would review query plans, add indexes based on actual query patterns, and use MongoDB aggregation and pagination to avoid loading large result sets into Python.