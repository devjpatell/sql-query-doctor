-- A small college-management schema (students, courses, enrollments, fee payments).
-- Inspired by the kind of system I worked on in production, written from scratch for this demo.
CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    dept_name TEXT NOT NULL,
    city TEXT
);

CREATE TABLE students (
    id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL,
    email TEXT,
    department_id INTEGER REFERENCES departments(id),
    category TEXT,
    status TEXT,
    admission_date TEXT
);

CREATE TABLE courses (
    id INTEGER PRIMARY KEY,
    course_name TEXT,
    department_id INTEGER REFERENCES departments(id),
    semester TEXT
);

CREATE TABLE enrollments (
    id INTEGER PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    course_id INTEGER REFERENCES courses(id),
    enrolled_at TEXT
);

CREATE TABLE fee_payments (
    id INTEGER PRIMARY KEY,
    student_id INTEGER REFERENCES students(id),
    amount REAL,
    method TEXT,
    status TEXT,
    paid_at TEXT
);
