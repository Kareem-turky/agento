// GET /api/product/approvals/approval?approval_id= -> Product API GET
// /api/v1/approvals/approval (the request, its safe summary and its append-only history).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "approval", { authorization: true, query: ["approval_id"] });
}
