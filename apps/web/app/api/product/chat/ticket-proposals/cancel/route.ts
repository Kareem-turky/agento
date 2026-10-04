// POST /api/product/chat/ticket-proposals/cancel -> Product API POST
// /api/v1/chat/ticket-proposals/cancel (Task 042: a durable, terminal cancellation).
import { proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "chatProposalCancel", { authorization: true, body: true });
}
