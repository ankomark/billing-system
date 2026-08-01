// Single source of truth for roles, matching billing/models.py User.
//
// These were renamed when the platform gained operators: admin/staff/superadmin
// became tenant_admin/tenant_staff plus a separate platform tier. Anything left
// comparing against the old names silently fails every check.

export const PLATFORM_OWNER = "platform_owner";
export const PLATFORM_STAFF = "platform_staff";
export const TENANT_ADMIN = "tenant_admin";
export const TENANT_STAFF = "tenant_staff";
export const CUSTOMER = "customer";

// Runs the platform. Sees every operator.
export const PLATFORM_ROLES = [PLATFORM_OWNER, PLATFORM_STAFF];

// Runs one WiFi business. Platform staff are included wherever operators are
// allowed, so support can act on an operator's behalf.
export const OPERATOR_ROLES = [TENANT_ADMIN, TENANT_STAFF, ...PLATFORM_ROLES];
export const OPERATOR_ADMIN_ROLES = [TENANT_ADMIN, ...PLATFORM_ROLES];

export function isPlatformRole(role) {
  return PLATFORM_ROLES.includes(role);
}

/**
 * Where an account lands after signing in.
 *
 * Platform staff get the master dashboard; operators get their own; everyone
 * else is a subscriber.
 */
export function homeFor(role) {
  if (isPlatformRole(role)) return "/platform";
  if (role === CUSTOMER) return "/customer/pppoe";
  return "/admin/dashboard";
}

/**
 * May this account change things, or only look?
 *
 * The sidebar answered this with its own `role !== "tenant_staff"` and the
 * pages did not answer it at all — so operator staff were shown Add Customer
 * and Delete buttons that could only ever produce a 403. One answer, in the
 * same file as the roles themselves.
 */
export function isOperatorAdmin(role) {
  return OPERATOR_ADMIN_ROLES.includes(role);
}
