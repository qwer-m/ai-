# Implementation Plan: User Authentication & Data Isolation

I will implement a complete authentication system with JWT tokens, ensuring all data is isolated per user.

## Backend Implementation (FastAPI)

1.  **Dependencies**: Add `passlib[bcrypt]` and `python-jose[cryptography]` to `requirements.txt`.
2.  **Database Models (`core/models.py`)**:
    *   Add `User` model (id, username, password_hash, created_at).
    *   Add `user_id` Foreign Key to all core models: `Project`, `KnowledgeDocument`, `TestGeneration`, `UIExecution`, `APIExecution`, `Evaluation`, `SystemConfig`, `LogEntry`.
3.  **Auth Core (`core/auth.py`)**:
    *   Implement password hashing (`verify_password`, `get_password_hash`).
    *   Implement JWT token handling (`create_access_token`).
    *   Create `get_current_user` dependency for route protection.
4.  **Auth Router (`modules/auth.py`)**:
    *   Create `router` with endpoints: `/auth/register`, `/auth/login`, `/auth/me`.
5.  **Secure Existing Modules**:
    *   Modify `modules/project.py` (if exists) or `main.py` and other modules (`knowledge_base.py`, etc.) to:
        *   Inject `current_user` dependency into all endpoints.
        *   **CRITICAL**: Filter all SQL queries with `.filter(Model.user_id == current_user.id)`.
        *   Ensure new records are created with `user_id=current_user.id`.
6.  **Main Application (`main.py`)**:
    *   Register the new `auth` router.
    *   Ensure `init_db` creates the new schema.

## Frontend Implementation (React)

1.  **Auth Context (`src/contexts/AuthContext.tsx`)**:
    *   Create a React Context to store `user` and `token`.
    *   Implement `login(username, password)` and `register(username, password)` methods.
    *   Persist token in `localStorage`.
2.  **API Utility (`src/utils/api.ts`)**:
    *   Add `setToken(token)` method.
    *   Update `request` function to automatically attach `Authorization: Bearer <token>` header if token exists.
    *   Handle `401 Unauthorized` responses by redirecting to login.
3.  **UI Components**:
    *   Create `src/components/Login.tsx`: Login form.
    *   Create `src/components/Register.tsx`: Registration form.
4.  **App Routing (`App.tsx`)**:
    *   Wrap the application in `AuthProvider`.
    *   Modify the main render loop:
        *   If `!isAuthenticated`: Show `Login` or `Register` component.
        *   If `isAuthenticated`: Show the main `Layout` (Sidebar + Content).

## Data Migration Note
*   **Important**: This change alters the database schema significantly (adding `user_id` to everything).
*   **Action**: I will update `init_db.py` to support the new schema.
*   **Effect**: Previous data (without `user_id`) will likely be inaccessible or I will need to reset the database (delete `ai_test_platform.db`) to ensure a clean state for testing isolation. **I will assume a database reset is acceptable for this dev environment.**

## Verification Plan
1.  **Register User A**: Verify success and auto-login.
2.  **Create Project**: Create "Project A" as User A.
3.  **Logout & Register User B**: Verify "Project A" is **NOT** visible.
4.  **Create Project**: Create "Project B" as User B.
5.  **Switch Users**: Confirm strict data isolation between A and B.
