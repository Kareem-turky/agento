// GET /api/product/conversations/conversation?conversation_id= -> Product API GET
// /api/v1/conversations/conversation (one conversation and its channel labels).
import { proxyToProduct } from "../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "conversation", { authorization: true, query: ["conversation_id"] });
}
