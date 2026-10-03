// GET /api/product/approvals?status=&action_name=&limit= -> Product API GET /api/v1/approvals
// (this company's human approval requests, newest first). There is no create route.
import { proxyToProduct } from "../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "approvals", { authorization: true, query: ["status", "action_name", "limit"] });
}
