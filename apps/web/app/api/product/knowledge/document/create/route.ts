// POST /api/product/knowledge/document/create -> Product API POST
// /api/v1/knowledge/document/create (text/plain or text/markdown only; no upload).
import { MAX_KNOWLEDGE_REQUEST_BYTES, proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "knowledgeDocumentCreate", {
    authorization: true,
    body: true,
    maxRequestBytes: MAX_KNOWLEDGE_REQUEST_BYTES,
  });
}
