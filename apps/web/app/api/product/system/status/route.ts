// GET /api/product/system/status -> Product API GET /api/v1/system/status (read-only,
// Product-authenticated: system.read). There is no system write route of any kind.
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "systemStatus", { authorization: true });
}
