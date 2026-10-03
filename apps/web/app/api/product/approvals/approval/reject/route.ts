// POST /api/product/approvals/approval/reject?approval_id= -> Product API POST
// /api/v1/approvals/approval/reject (a human decision; JSON body: {note} — required reason).
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "approvalReject", { authorization: true, query: ["approval_id"], body: true });
}
