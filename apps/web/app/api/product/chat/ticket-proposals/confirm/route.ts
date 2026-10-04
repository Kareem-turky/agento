// POST /api/product/chat/ticket-proposals/confirm -> Product API POST
// /api/v1/chat/ticket-proposals/confirm (Task 042: the explicit human confirmation of a
// STORED ticket proposal: {proposal_id} plus exactly one Idempotency-Key).
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "chatProposalConfirm", {
    authorization: true,
    idempotencyKey: true,
    body: true,
  });
}
