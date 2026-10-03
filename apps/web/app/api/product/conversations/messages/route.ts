// GET /api/product/conversations/messages?conversation_id=&before_sequence=&limit= ->
// Product API GET /api/v1/conversations/messages (a bounded transcript page).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "conversationMessages", {
    authorization: true,
    query: ["conversation_id", "before_sequence", "limit"],
  });
}
