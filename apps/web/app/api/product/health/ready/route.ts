// GET /api/product/health/ready -> Product API GET /health/ready only (no credential
// forwarded). The Web container health check uses it: Web is healthy only while the
// Product installation is READY. The answer is the minimal status (200 ready / 503
// not_ready) and never carries a reason.
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "healthReady", { authorization: false });
}
