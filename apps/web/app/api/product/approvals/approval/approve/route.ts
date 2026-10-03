// POST /api/product/approvals/approval/approve?approval_id= -> Product API POST
// /api/v1/approvals/approval/approve (a human decision; JSON body: {note} — optional note).
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "approvalApprove", { authorization: true, query: ["approval_id"], body: true });
}
