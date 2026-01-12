---
title: 'SPEC-13: CLI Authentication with Subscription Validation'
type: spec
permalink: specs/spec-12-cli-auth-subscription-validation
tags:
- authentication
- security
- cli
- subscription
status: draft
created: 2025-10-02
---

# SPEC-13: CLI Authentication with Subscription Validation

## Why

The Basic Memory Cloud CLI currently has a security gap in authentication that allows unauthorized access:

**Current Web Flow (Secure)**:
1. User signs up via WorkOS AuthKit
2. User creates Polar subscription
3. Web app validates subscription before calling `POST /tenants/setup`
4. Tenant provisioned only after subscription validation ✅

**Current CLI Flow (Insecure)**:
1. User signs up via WorkOS AuthKit (OAuth device flow)
2. User runs `bm cloud login`
3. CLI receives JWT token from WorkOS
4. CLI can access all cloud endpoints without subscription check ❌

**Problem**: Anyone can sign up with WorkOS and immediately access cloud infrastructure via CLI without having an active Polar subscription. This creates:
- Revenue loss (free resource consumption)
- Security risk (unauthorized data access)
- Support burden (users accessing features they haven't paid for)

**Root Cause**: The CLI authentication flow validates JWT tokens but doesn't verify subscription status before granting access to cloud resources.

## What

Add subscription validation to authentication flow to ensure only users with active Polar subscriptions can access cloud resources across all access methods (CLI, MCP, Web App, Direct API).

**Affected Components**:

### basic-memory-cloud (Cloud Service)
- `apps/cloud/src/basic_memory_cloud/deps.py` - Add subscription validation dependency
- `apps/cloud/src/basic_memory_cloud/services/subscription_service.py` - Add subscription check method
- `apps/cloud/src/basic_memory_cloud/api/tenant_mount.py` - Protect mount endpoints
- `apps/cloud/src/basic_memory_cloud/api/proxy.py` - Protect proxy endpoints

### basic-memory (CLI)
- `src/basic_memory/cli/commands/cloud/core_commands.py` - Handle 403 errors
- `src/basic_memory/cli/commands/cloud/api_client.py` - Parse subscription errors
- `docs/cloud-cli.md` - Document subscription requirement

**Endpoints to Protect**:
- `GET /tenant/mount/info` - Used by CLI bisync setup
- `POST /tenant/mount/credentials` - Used by CLI bisync credentials
- `GET /proxy/{path:path}` - Used by Web App, MCP tools, CLI tools, Direct API
- All other `/proxy/*` endpoints - Centralized access point for all user operations

## Complete Authentication Flow Analysis

### Overview of All Access Flows

Basic Memory Cloud has **7 distinct authentication flows**. This spec closes subscription validation gaps in flows 2-4 and 6, which all converge on the `/proxy/*` endpoints.

### Flow 1: Polar Webhook → Registration ✅ SECURE
```
Polar webhook → POST /api/webhooks/polar
→ Validates Polar webhook signature
→ Creates/updates subscription in database
→ No direct user access - webhook only
```
**Auth**: Polar webhook signature validation
**Subscription Check**: N/A (webhook creates subscriptions)
**Status**: ✅ Secure - webhook validated, no user JWT involved

### Flow 2: Web App Login ❌ NEEDS FIX
```
User → apps/web (Vue.js/Nuxt)
→ WorkOS AuthKit magic link authentication
→ JWT stored in browser session
→ Web app calls /proxy/{project}/... endpoints (memory, directory, projects)
→ proxy.py validates JWT but does NOT check subscription
→ Access granted without subscription ❌
```
**Auth**: WorkOS JWT via `CurrentUserProfileHybridJwtDep`
**Subscription Check**: ❌ Missing
**Fixed By**: Task 1.4 (protect `/proxy/*` endpoints)

### Flow 3: MCP (Model Context Protocol) ❌ NEEDS FIX
```
AI Agent (Claude, Cursor, etc.) → https://mcp.basicmemory.com
→ AuthKit OAuth device flow
→ JWT stored in AI agent
→ MCP tools call {cloud_host}/proxy/{endpoint} with Authorization header
→ proxy.py validates JWT but does NOT check subscription
→ MCP tools can access all cloud resources without subscription ❌
```
**Auth**: AuthKit JWT via `CurrentUserProfileHybridJwtDep`
**Subscription Check**: ❌ Missing
**Fixed By**: Task 1.4 (protect `/proxy/*` endpoints)

### Flow 4: CLI Auth (basic-memory) ❌ NEEDS FIX
```
User → bm cloud login
→ AuthKit OAuth device flow
→ JWT stored in ~/.basic-memory/tokens.json
→ CLI calls:
  - {cloud_host}/tenant/mount/info (for bisync setup)
  - {cloud_host}/tenant/mount/credentials (for bisync credentials)
  - {cloud_host}/proxy/{endpoint} (for all MCP tools)
→ tenant_mount.py and proxy.py validate JWT but do NOT check subscription
→ Access granted without subscription ❌
```
**Auth**: AuthKit JWT via `CurrentUserProfileHybridJwtDep`
**Subscription Check**: ❌ Missing
**Fixed By**: Task 1.3 (protect `/tenant/mount/*`) + Task 1.4 (protect `/proxy/*`)

### Flow 5: Cloud CLI (Admin Tasks) ✅ SECURE
```
Admin → python -m basic_memory_cloud.cli.tenant_cli
→ Uses CLIAuth with admin WorkOS OAuth client
→ Gets JWT token with admin org membership
→ Calls /tenants/* endpoints (create, list, delete tenants)
→ tenants.py validates JWT AND admin org membership via AdminUserHybridDep
→ Access granted only to admin organization members ✅
```
**Auth**: AuthKit JWT + Admin org validation via `AdminUserHybridDep`
**Subscription Check**: N/A (admins bypass subscription requirement)
**Status**: ✅ Secure - admin-only endpoints, separate from user flows

### Flow 6: Direct API Calls ❌ NEEDS FIX
```
Any HTTP client → {cloud_host}/proxy/{endpoint}
→ Sends Authorization: Bearer {jwt} header
→ proxy.py validates JWT but does NOT check subscription
→ Direct API access without subscription ❌
```
**Auth**: WorkOS or AuthKit JWT via `CurrentUserProfileHybridJwtDep`
**Subscription Check**: ❌ Missing
**Fixed By**: Task 1.4 (protect `/proxy/*` endpoints)

### Flow 7: Tenant API Instance (Internal) ✅ SECURE
```
/proxy/* → Tenant API (basic-memory-{tenant_id}.fly.dev)
→ Validates signed header from proxy (tenant_id + signature)
→ Direct external access will be disabled in production
→ Only accessible via /proxy endpoints
```
**Auth**: Signed header validation from proxy
**Subscription Check**: N/A (internal only, validated at proxy layer)
**Status**: ✅ Secure - validates proxy signature, not directly accessible

### Authentication Flow Summary Matrix

| Flow | Access Method | Current Auth | Subscription Check | Fixed By SPEC-13 |
|------|---------------|--------------|-------------------|------------------|
| 1. Polar Webhook | Polar webhook → `/api/webhooks/polar` | Polar signature | N/A (webhook) | N/A |
| 2. Web App | Browser → `/proxy/*` | WorkOS JWT ✅ | ❌ Missing | ✅ Task 1.4 |
| 3. MCP | AI Agent → `/proxy/*` | AuthKit JWT ✅ | ❌ Missing | ✅ Task 1.4 |
| 4. CLI | `bm cloud` → `/tenant/mount/*` + `/proxy/*` | AuthKit JWT ✅ | ❌ Missing | ✅ Task 1.3 + 1.4 |
| 5. Cloud CLI (Admin) | `tenant_cli` → `/tenants/*` | AuthKit JWT ✅ + Admin org | N/A (admin) | N/A (admin bypass) |
| 6. Direct API | HTTP client → `/proxy/*` | WorkOS/AuthKit JWT ✅ | ❌ Missing | ✅ Task 1.4 |
| 7. Tenant API | Proxy → tenant instance | Proxy signature ✅ | N/A (internal) | N/A |

### Key Insights

1. **Single Point of Failure**: All user access (Web, MCP, CLI, Direct API) converges on `/proxy/*` endpoints
2. **Centralized Fix**: Protecting `/proxy/*` with subscription validation closes gaps in flows 2, 3, 4, and 6 simultaneously
3. **Admin Bypass**: Cloud CLI admin tasks use separate `/tenants/*` endpoints with admin-only access (no subscription needed)
4. **Defense in Depth**: `/tenant/mount/*` endpoints also protected for CLI bisync operations

### Architecture Benefits

The `/proxy` layer serves as the **single centralized authorization point** for all user access:
- ✅ One place to validate JWT tokens
- ✅ One place to check subscription status
- ✅ One place to handle tenant routing
- ✅ Protects Web App, MCP, CLI, and Direct API simultaneously

This architecture makes the fix comprehensive and maintainable.

## How (High Level)

### Option A: Database Subscription Check (Recommended)

**Approach**: Add FastAPI dependency that validates subscription status from database before allowing access.

**Implementation**:

1. **Create Subscription Validation Dependency** (`deps.py`)
   ```python
   async def get_authorized_cli_user_profile(
       credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
       session: DatabaseSessionDep,
       user_profile_repo: UserProfileRepositoryDep,
       subscription_service: SubscriptionServiceDep,
   ) -> UserProfile:
       """
       Hybrid authentication with subscription validation for CLI access.

       Validates JWT (WorkOS or AuthKit) and checks for active subscription.
       Returns UserProfile if both checks pass.
       """
       # Try WorkOS JWT first (faster validation path)
       try:
           user_context = await validate_workos_jwt(credentials.credentials)
       except HTTPException:
           # Fall back to AuthKit JWT validation
           try:
               user_context = await validate_authkit_jwt(credentials.credentials)
           except HTTPException as e:
               raise HTTPException(
                   status_code=401,
                   detail="Invalid JWT token. Authentication required.",
               ) from e

       # Check subscription status
       has_subscription = await subscription_service.check_user_has_active_subscription(
           session, user_context.workos_user_id
       )

       if not has_subscription:
           raise HTTPException(
               status_code=403,
               detail={
                   "error": "subscription_required",
                   "message": "Active subscription required for CLI access",
                   "subscribe_url": "https://basicmemory.com/subscribe"
               }
           )

       # Look up and return user profile
       user_profile = await user_profile_repo.get_user_profile_by_workos_user_id(
           session, user_context.workos_user_id
       )
       if not user_profile:
           raise HTTPException(401, detail="User profile not found")

       return user_profile
   ```

   ```python
   AuthorizedCLIUserProfileDep = Annotated[UserProfile, Depends(get_authorized_cli_user_profile)]
   ```

2. **Add Subscription Check Method** (`subscription_service.py`)
   ```python
   async def check_user_has_active_subscription(
       self, session: AsyncSession, workos_user_id: str
   ) -> bool:
       """Check if user has active subscription."""
       # Use existing repository method to get subscription by workos_user_id
       # This joins UserProfile -> Subscription in a single query
       subscription = await self.subscription_repository.get_subscription_by_workos_user_id(
           session, workos_user_id
       )

       return subscription is not None and subscription.status == "active"
   ```

3. **Protect Endpoints** (Replace `CurrentUserProfileHybridJwtDep` with `AuthorizedCLIUserProfileDep`)
   ```python
   # Before
   @router.get("/mount/info")
   async def get_mount_info(
       user_profile: CurrentUserProfileHybridJwtDep,
       session: DatabaseSessionDep,
   ):
       tenant_id = user_profile.tenant_id
       ...

   # After
   @router.get("/mount/info")
   async def get_mount_info(
       user_profile: AuthorizedCLIUserProfileDep,  # Now includes subscription check
       session: DatabaseSessionDep,
   ):
       tenant_id = user_profile.tenant_id  # No changes needed to endpoint logic
       ...
   ```

4. **Update CLI Error Handling**
   ```python
   # In core_commands.py login()
   try:
       success = await auth.login()
       if success:
           # Test subscription by calling protected endpoint
           await make_api_request("GET", f"{host_url}/tenant/mount/info")
   except CloudAPIError as e:
       if e.status_code == 403 and e.detail.get("error") == "subscription_required":
           console.print("[red]Subscription required[/red]")
           console.print(f"Subscribe at: {e.detail['subscribe_url']}")
           raise typer.Exit(1)
   ```

**Pros**:
- Simple to implement
- Fast (single database query)
- Clear error messages
- Works with existing subscription flow

**Cons**:
- Database is source of truth (could get out of sync with Polar)
- Adds one extra subscription lookup query per request (lightweight JOIN query)

### Option B: WorkOS Organizations

**Approach**: Add users to "beta-users" organization in WorkOS after subscription creation, validate org membership via JWT claims.

**Implementation**:
1. After Polar subscription webhook, add user to WorkOS org via API
2. Validate `org_id` claim in JWT matches authorized org
3. Use existing `get_admin_workos_jwt` pattern

**Pros**:
- WorkOS as single source of truth
- No database queries needed
- More secure (harder to bypass)

**Cons**:
- More complex (requires WorkOS API integration)
- Requires managing WorkOS org membership
- Less control over error messages
- Additional API calls during registration

### Recommendation

**Start with Option A (Database Check)** for:
- Faster implementation
- Clearer error messages
- Easier testing
- Existing subscription infrastructure

**Consider Option B later** if:
- Need tighter security
- Want to reduce database dependency
- Scale requires fewer database queries

## How to Evaluate

### Success Criteria

**1. Unauthorized Users Blocked**
- [ ] User without subscription cannot complete `bm cloud login`
- [ ] User without subscription receives clear error with subscribe link
- [ ] User without subscription cannot run `bm cloud setup`
- [ ] User without subscription cannot run `bm sync` in cloud mode

**2. Authorized Users Work**
- [ ] User with active subscription can login successfully
- [ ] User with active subscription can setup bisync
- [ ] User with active subscription can sync files
- [ ] User with active subscription can use all MCP tools via proxy

**3. Subscription State Changes**
- [ ] Expired subscription blocks access with clear error
- [ ] Renewed subscription immediately restores access
- [ ] Cancelled subscription blocks access after grace period

**4. Error Messages**
- [ ] 403 errors include "subscription_required" error code
- [ ] Error messages include subscribe URL
- [ ] CLI displays user-friendly messages
- [ ] Errors logged appropriately for debugging

**5. No Regressions**
- [ ] Web app login/subscription flow unaffected
- [ ] Admin endpoints still work (bypass check)
- [ ] Tenant provisioning workflow unchanged
- [ ] Performance not degraded

### Test Cases

**Manual Testing**:
```bash
# Test 1: Unauthorized user
1. Create new WorkOS account (no subscription)
2. Run `bm cloud login`
3. Verify: Login succeeds but shows subscription required error
4. Verify: Cannot run `bm cloud setup`
5. Verify: Clear error message with subscribe link

# Test 2: Authorized user
1. Use account with active Polar subscription
2. Run `bm cloud login`
3. Verify: Login succeeds without errors
4. Run `bm cloud setup`
5. Verify: Setup completes successfully
6. Run `bm sync`
7. Verify: Sync works normally

# Test 3: Subscription expiration
1. Use account with active subscription
2. Manually expire subscription in database
3. Run `bm cloud login`
4. Verify: Blocked with clear error
5. Renew subscription
6. Run `bm cloud login` again
7. Verify: Access restored
```

**Automated Tests**:
```python
# Test subscription validation dependency
async def test_authorized_user_allowed(
    db_session,
    user_profile_repo,
    subscription_service,
    mock_jwt_credentials
):
    # Create user with active subscription
    user_profile = await create_user_with_subscription(db_session, status="active")

    # Mock JWT credentials for the user
    credentials = mock_jwt_credentials(user_profile.workos_user_id)

    # Should not raise exception
    result = await get_authorized_cli_user_profile(
        credentials, db_session, user_profile_repo, subscription_service
    )
    assert result.id == user_profile.id
    assert result.workos_user_id == user_profile.workos_user_id

async def test_unauthorized_user_blocked(
    db_session,
    user_profile_repo,
    subscription_service,
    mock_jwt_credentials
):
    # Create user without subscription
    user_profile = await create_user_without_subscription(db_session)
    credentials = mock_jwt_credentials(user_profile.workos_user_id)

    # Should raise 403
    with pytest.raises(HTTPException) as exc:
        await get_authorized_cli_user_profile(
            credentials, db_session, user_profile_repo, subscription_service
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "subscription_required"

async def test_inactive_subscription_blocked(
    db_session,
    user_profile_repo,
    subscription_service,
    mock_jwt_credentials
):
    # Create user with cancelled/inactive subscription
    user_profile = await create_user_with_subscription(db_session, status="cancelled")
    credentials = mock_jwt_credentials(user_profile.workos_user_id)

    # Should raise 403
    with pytest.raises(HTTPException) as exc:
        await get_authorized_cli_user_profile(
            credentials, db_session, user_profile_repo, subscription_service
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "subscription_required"
```

## Implementation Tasks

### Phase 1: Cloud Service (basic-memory-cloud)

#### Task 1.1: Add subscription check method to SubscriptionService ✅
**File**: `apps/cloud/src/basic_memory_cloud/services/subscription_service.py`

- [x] Add method `check_subscription(session: AsyncSession, workos_user_id: str) -> bool`
- [x] Use existing `self.subscription_repository.get_subscription_by_workos_user_id(session, workos_user_id)`
- [x] Check both `status == "active"` AND `current_period_end >= now()`
- [x] Log both values when check fails
- [x] Add docstring explaining the method
- [x] Run `just typecheck` to verify types

**Actual implementation**:
```python
async def check_subscription(
    self, session: AsyncSession, workos_user_id: str
) -> bool:
    """Check if user has active subscription with valid period."""
    subscription = await self.subscription_repository.get_subscription_by_workos_user_id(
        session, workos_user_id
    )

    if subscription is None:
        return False

    if subscription.status != "active":
        logger.warning("Subscription inactive", workos_user_id=workos_user_id,
                      status=subscription.status, current_period_end=subscription.current_period_end)
        return False

    now = datetime.now(timezone.utc)
    if subscription.current_period_end is None or subscription.current_period_end < now:
        logger.warning("Subscription expired", workos_user_id=workos_user_id,
                      status=subscription.status, current_period_end=subscription.current_period_end)
        return False

    return True
```

#### Task 1.2: Add subscription validation dependency ✅
**File**: `apps/cloud/src/basic_memory_cloud/deps.py`

- [x] Import necessary types at top of file (if not already present)
- [x] Add `authorized_user_profile()` async function
- [x] Implement hybrid JWT validation (WorkOS first, AuthKit fallback)
- [x] Add subscription check using `subscription_service.check_subscription()`
- [x] Raise `HTTPException(403)` with structured error detail if no active subscription
- [x] Look up and return `UserProfile` after validation
- [x] Add `AuthorizedUserProfileDep` type annotation
- [x] Use `settings.subscription_url` from config (env var)
- [x] Run `just typecheck` to verify types

**Expected code**:
```python
async def get_authorized_cli_user_profile(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    session: DatabaseSessionDep,
    user_profile_repo: UserProfileRepositoryDep,
    subscription_service: SubscriptionServiceDep,
) -> UserProfile:
    """
    Hybrid authentication with subscription validation for CLI access.

    Validates JWT (WorkOS or AuthKit) and checks for active subscription.
    Returns UserProfile if both checks pass.

    Raises:
        HTTPException(401): Invalid JWT token
        HTTPException(403): No active subscription
    """
    # Try WorkOS JWT first (faster validation path)
    try:
        user_context = await validate_workos_jwt(credentials.credentials)
    except HTTPException:
        # Fall back to AuthKit JWT validation
        try:
            user_context = await validate_authkit_jwt(credentials.credentials)
        except HTTPException as e:
            raise HTTPException(
                status_code=401,
                detail="Invalid JWT token. Authentication required.",
            ) from e

    # Check subscription status
    has_subscription = await subscription_service.check_user_has_active_subscription(
        session, user_context.workos_user_id
    )

    if not has_subscription:
        logger.warning(
            "CLI access denied: no active subscription",
            workos_user_id=user_context.workos_user_id,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "subscription_required",
                "message": "Active subscription required for CLI access",
                "subscribe_url": "https://basicmemory.com/subscribe"
            }
        )

    # Look up and return user profile
    user_profile = await user_profile_repo.get_user_profile_by_workos_user_id(
        session, user_context.workos_user_id
    )
    if not user_profile:
        logger.error(
            "User profile not found after successful auth",
            workos_user_id=user_context.workos_user_id,
        )
        raise HTTPException(401, detail="User profile not found")

    logger.info(
        "CLI access granted",
        workos_user_id=user_context.workos_user_id,
        user_profile_id=str(user_profile.id),
    )
    return user_profile


AuthorizedCLIUserProfileDep = Annotated[UserProfile, Depends(get_authorized_cli_user_profile)]
```

#### Task 1.3: Protect tenant mount endpoints ✅
**File**: `apps/cloud/src/basic_memory_cloud/api/tenant_mount.py`

- [x] Update import: add `AuthorizedUserProfileDep` from `..deps`
- [x] Replace `user_profile: CurrentUserProfileHybridJwtDep` with `user_profile: AuthorizedUserProfileDep` in:
  - [x] `get_tenant_mount_info()` (line ~23)
  - [x] `create_tenant_mount_credentials()` (line ~88)
  - [x] `revoke_tenant_mount_credentials()` (line ~244)
  - [x] `list_tenant_mount_credentials()` (line ~326)
- [x] Verify no other code changes needed (parameter name and usage stays the same)
- [x] Run `just typecheck` to verify types

#### Task 1.4: Protect proxy endpoints ✅
**File**: `apps/cloud/src/basic_memory_cloud/api/proxy.py`

- [x] Update import: add `AuthorizedUserProfileDep` from `..deps`
- [x] Replace `user_profile: CurrentUserProfileHybridJwtDep` with `user_profile: AuthorizedUserProfileDep` in:
  - [x] `check_tenant_health()` (line ~21)
  - [x] `proxy_to_tenant()` (line ~63)
- [x] Verify no other code changes needed (parameter name and usage stays the same)
- [x] Run `just typecheck` to verify types

**Why Keep /proxy Architecture:**

The proxy layer is valuable because it:
1. **Centralizes authorization** - Single place for JWT + subscription validation (closes both CLI and MCP auth gaps)
2. **Handles tenant routing** - Maps tenant_id → fly_app_name without exposing infrastructure details
3. **Abstracts infrastructure** - MCP and CLI don't need to know about Fly.io naming conventions
4. **Enables features** - Can add rate limiting, caching, request logging, etc. at proxy layer
5. **Supports both flows** - CLI tools and MCP tools both use /proxy endpoints

The extra HTTP hop is minimal (< 10ms) and worth it for architectural benefits.

**Performance Note:** Cloud app has Redis available - can cache subscription status to reduce database queries if needed. Initial implementation uses direct database query (simple, acceptable performance ~5-10ms).

#### Task 1.5: Add unit tests for subscription service
**File**: `apps/cloud/tests/services/test_subscription_service.py` (create if doesn't exist)

- [ ] Create test file if it doesn't exist
- [ ] Add test: `test_check_user_has_active_subscription_returns_true_for_active()`
  - Create user with active subscription
  - Call `check_user_has_active_subscription()`
  - Assert returns `True`
- [ ] Add test: `test_check_user_has_active_subscription_returns_false_for_pending()`
  - Create user with pending subscription
  - Assert returns `False`
- [ ] Add test: `test_check_user_has_active_subscription_returns_false_for_cancelled()`
  - Create user with cancelled subscription
  - Assert returns `False`
- [ ] Add test: `test_check_user_has_active_subscription_returns_false_for_no_subscription()`
  - Create user without subscription
  - Assert returns `False`
- [ ] Run `just test` to verify tests pass

#### Task 1.6: Add integration tests for dependency
**File**: `apps/cloud/tests/test_deps.py` (create if doesn't exist)

- [ ] Create test file if it doesn't exist
- [ ] Add fixtures for mocking JWT credentials
- [ ] Add test: `test_authorized_cli_user_profile_with_active_subscription()`
  - Mock valid JWT + active subscription
  - Call dependency
  - Assert returns UserProfile
- [ ] Add test: `test_authorized_cli_user_profile_without_subscription_raises_403()`
  - Mock valid JWT + no subscription
  - Assert raises HTTPException(403) with correct error detail
- [ ] Add test: `test_authorized_cli_user_profile_with_inactive_subscription_raises_403()`
  - Mock valid JWT + cancelled subscription
  - Assert raises HTTPException(403)
- [ ] Add test: `test_authorized_cli_user_profile_with_invalid_jwt_raises_401()`
  - Mock invalid JWT
  - Assert raises HTTPException(401)
- [ ] Run `just test` to verify tests pass

#### Task 1.7: Deploy and verify cloud service
- [ ] Run `just check` to verify all quality checks pass
- [ ] Commit changes with message: "feat: add subscription validation to CLI endpoints"
- [ ] Deploy to preview environment: `flyctl deploy --config apps/cloud/fly.toml`
- [ ] Test manually:
  - [ ] Call `/tenant/mount/info` with valid JWT but no subscription → expect 403
  - [ ] Call `/tenant/mount/info` with valid JWT and active subscription → expect 200
  - [ ] Verify error response structure matches spec

### Phase 2: CLI (basic-memory)

#### Task 2.1: Review and understand CLI authentication flow
**Files**: `src/basic_memory/cli/commands/cloud/`

- [ ] Read `core_commands.py` to understand current login flow
- [ ] Read `api_client.py` to understand current error handling
- [ ] Identify where 403 errors should be caught
- [ ] Identify what error messages should be displayed
- [ ] Document current behavior in spec if needed

#### Task 2.2: Update API client error handling
**File**: `src/basic_memory/cli/commands/cloud/api_client.py`

- [ ] Add custom exception class `SubscriptionRequiredError` (or similar)
- [ ] Update HTTP error handling to parse 403 responses
- [ ] Extract `error`, `message`, and `subscribe_url` from error detail
- [ ] Raise specific exception for subscription_required errors
- [ ] Run `just typecheck` in basic-memory repo to verify types

#### Task 2.3: Update CLI login command error handling
**File**: `src/basic_memory/cli/commands/cloud/core_commands.py`

- [ ] Import the subscription error exception
- [ ] Wrap login flow with try/except for subscription errors
- [ ] Display user-friendly error message with rich console
- [ ] Show subscribe URL prominently
- [ ] Provide actionable next steps
- [ ] Run `just typecheck` to verify types

**Expected error handling**:
```python
try:
    # Existing login logic
    success = await auth.login()
    if success:
        # Test access to protected endpoint
        await api_client.test_connection()
except SubscriptionRequiredError as e:
    console.print("\n[red]✗ Subscription Required[/red]\n")
    console.print(f"[yellow]{e.message}[/yellow]\n")
    console.print(f"Subscribe at: [blue underline]{e.subscribe_url}[/blue underline]\n")
    console.print("[dim]Once you have an active subscription, run [bold]bm cloud login[/bold] again.[/dim]")
    raise typer.Exit(1)
```

#### Task 2.4: Update CLI tests
**File**: `tests/cli/test_cloud_commands.py`

- [ ] Add test: `test_login_without_subscription_shows_error()`
  - Mock 403 subscription_required response
  - Call login command
  - Assert error message displayed
  - Assert subscribe URL shown
- [ ] Add test: `test_login_with_subscription_succeeds()`
  - Mock successful authentication + subscription check
  - Call login command
  - Assert success message
- [ ] Run `just test` to verify tests pass

#### Task 2.5: Update CLI documentation
**File**: `docs/cloud-cli.md` (in basic-memory-docs repo)

- [ ] Add "Prerequisites" section if not present
- [ ] Document subscription requirement
- [ ] Add "Troubleshooting" section
- [ ] Document "Subscription Required" error
- [ ] Provide subscribe URL
- [ ] Add FAQ entry about subscription errors
- [ ] Build docs locally to verify formatting

### Phase 3: End-to-End Testing

#### Task 3.1: Create test user accounts
**Prerequisites**: Access to WorkOS admin and database

- [ ] Create test user WITHOUT subscription:
  - [ ] Sign up via WorkOS AuthKit
  - [ ] Get workos_user_id from database
  - [ ] Verify no subscription record exists
  - [ ] Save credentials for testing
- [ ] Create test user WITH active subscription:
  - [ ] Sign up via WorkOS AuthKit
  - [ ] Create subscription via Polar or dev endpoint
  - [ ] Verify subscription.status = "active" in database
  - [ ] Save credentials for testing

#### Task 3.2: Manual testing - User without subscription
**Environment**: Preview/staging deployment

- [ ] Run `bm cloud login` with no-subscription user
- [ ] Verify: Login shows "Subscription Required" error
- [ ] Verify: Subscribe URL is displayed
- [ ] Verify: Cannot run `bm cloud setup`
- [ ] Verify: Cannot call `/tenant/mount/info` directly via curl
- [ ] Document any issues found

#### Task 3.3: Manual testing - User with active subscription
**Environment**: Preview/staging deployment

- [ ] Run `bm cloud login` with active-subscription user
- [ ] Verify: Login succeeds without errors
- [ ] Verify: Can run `bm cloud setup`
- [ ] Verify: Can call `/tenant/mount/info` successfully
- [ ] Verify: Can call `/proxy/*` endpoints successfully
- [ ] Document any issues found

#### Task 3.4: Test subscription state transitions
**Environment**: Preview/staging deployment + database access

- [ ] Start with active subscription user
- [ ] Verify: All operations work
- [ ] Update subscription.status to "cancelled" in database
- [ ] Verify: Login now shows "Subscription Required" error
- [ ] Verify: Existing tokens are rejected with 403
- [ ] Update subscription.status back to "active"
- [ ] Verify: Access restored immediately
- [ ] Document any issues found

#### Task 3.5: Integration test suite
**File**: `apps/cloud/tests/integration/test_cli_subscription_flow.py` (create if doesn't exist)

- [ ] Create integration test file
- [ ] Add test: `test_cli_flow_without_subscription()`
  - Simulate full CLI flow without subscription
  - Assert 403 at appropriate points
- [ ] Add test: `test_cli_flow_with_active_subscription()`
  - Simulate full CLI flow with active subscription
  - Assert all operations succeed
- [ ] Add test: `test_subscription_expiration_blocks_access()`
  - Start with active subscription
  - Change status to cancelled
  - Assert access denied
- [ ] Run tests in CI/CD pipeline
- [ ] Document test coverage

#### Task 3.6: Load/performance testing (optional)
**Environment**: Staging environment

- [ ] Test subscription check performance under load
- [ ] Measure latency added by subscription check
- [ ] Verify database query performance
- [ ] Document any performance concerns
- [ ] Optimize if needed

## Implementation Summary Checklist

Use this high-level checklist to track overall progress:

### Phase 1: Cloud Service 🔄
- [x] Add subscription check method to SubscriptionService
- [x] Add subscription validation dependency to deps.py
- [x] Add subscription_url config (env var)
- [x] Protect tenant mount endpoints (4 endpoints)
- [x] Protect proxy endpoints (2 endpoints)
- [ ] Add unit tests for subscription service
- [ ] Add integration tests for dependency
- [ ] Deploy and verify cloud service

### Phase 2: CLI Updates 🔄
- [ ] Review CLI authentication flow
- [ ] Update API client error handling
- [ ] Update CLI login command error handling
- [ ] Add CLI tests
- [ ] Update CLI documentation

### Phase 3: End-to-End Testing 🧪
- [ ] Create test user accounts
- [ ] Manual testing - user without subscription
- [ ] Manual testing - user with active subscription
- [ ] Test subscription state transitions
- [ ] Integration test suite
- [ ] Load/performance testing (optional)

## Questions to Resolve

### Resolved ✅

1. **Admin Access**
   - ✅ **Decision**: Admin users bypass subscription check
   - **Rationale**: Admin endpoints already use `AdminUserHybridDep`, which is separate from CLI user endpoints
   - **Implementation**: No changes needed to admin endpoints

2. **Subscription Check Implementation**
   - ✅ **Decision**: Use Option A (Database Check)
   - **Rationale**: Simpler, faster to implement, works with existing infrastructure
   - **Implementation**: Single JOIN query via `get_subscription_by_workos_user_id()`

3. **Dependency Return Type**
   - ✅ **Decision**: Return `UserProfile` (not `UserContext`)
   - **Rationale**: Drop-in compatibility with existing endpoints, no refactoring needed
   - **Implementation**: `AuthorizedCLIUserProfileDep` returns `UserProfile`

### To Be Resolved ⏳

1. **Subscription Check Frequency**
   - **Options**:
     - Check on every API call (slower, more secure) ✅ **RECOMMENDED**
     - Cache subscription status (faster, risk of stale data)
     - Check only on login/setup (fast, but allows expired subscriptions temporarily)
   - **Recommendation**: Check on every call via dependency injection (simple, secure, acceptable performance)
   - **Impact**: ~5-10ms per request (single indexed JOIN query)

2. **Grace Period**
   - **Options**:
     - No grace period - immediate block when status != "active" ✅ **RECOMMENDED**
     - 7-day grace period after period_end
     - 14-day grace period after period_end
   - **Recommendation**: No grace period initially, add later if needed based on customer feedback
   - **Implementation**: Check `subscription.status == "active"` only (ignore period_end initially)

3. **Subscription Expiration Handling**
   - **Question**: Should we check `current_period_end < now()` in addition to `status == "active"`?
   - **Options**:
     - Only check status field (rely on Polar webhooks to update status) ✅ **RECOMMENDED**
     - Check both status and current_period_end (more defensive)
   - **Recommendation**: Only check status field, assume Polar webhooks keep it current
   - **Risk**: If webhooks fail, expired subscriptions might retain access until webhook succeeds

4. **Subscribe URL**
   - **Question**: What's the actual subscription URL?
   - **Current**: Spec uses `https://basicmemory.com/subscribe`
   - **Action Required**: Verify correct URL before implementation

5. **Dev Mode / Testing Bypass**
   - **Question**: Support bypass for development/testing?
   - **Options**:
     - Environment variable: `DISABLE_SUBSCRIPTION_CHECK=true`
     - Always enforce (more realistic testing) ✅ **RECOMMENDED**
   - **Recommendation**: No bypass - use test users with real subscriptions for realistic testing
   - **Implementation**: Create dev endpoint to activate subscriptions for testing

## Related Specs

- SPEC-9: Multi-Project Bidirectional Sync Architecture (CLI affected by this change)
- SPEC-8: TigrisFS Integration (Mount endpoints protected)

## Notes

- This spec prioritizes security over convenience - better to block unauthorized access than risk revenue loss
- Clear error messages are critical - users should understand why they're blocked and how to resolve it
- Consider adding telemetry to track subscription_required errors for monitoring signup conversion
