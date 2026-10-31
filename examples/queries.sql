-- 1. Pending fee payments for active students in one category, newest first
SELECT s.full_name, f.amount, f.paid_at
FROM fee_payments f
JOIN students s ON s.id = f.student_id
WHERE f.status = 'pending' AND s.category = 'ews' AND f.paid_at >= '2026-01-01'
ORDER BY f.paid_at DESC;

-- 2. Fee report for one student
SELECT amount, method, paid_at FROM fee_payments WHERE student_id = 4211 ORDER BY paid_at;

-- 3. Course roster
SELECT s.full_name, s.email
FROM enrollments e
JOIN students s ON s.id = e.student_id
WHERE e.course_id = 120;
