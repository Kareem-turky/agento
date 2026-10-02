// /api/product/knowledge/document/version -> Product API /api/v1/knowledge/document/version
// GET ?document_id=&version= reads one immutable version; POST ?document_id= publishes the
// next version (text only, governed and audited by the Product API).
import { MAX_KNOWLEDGE_REQUEST_BYTES, proxyToProduct } from "../../../../../../lib/product-api/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request): Promise<Response> {
  return proxyToProduct(request, "knowledgeDocumentVersion", { authorization: true, query: ["document_id", "version"] });
}

export function POST(request: Request): Promise<Response> {
  return proxyToProduct(request, "knowledgeDocumentPublishVersion", {
    authorization: true,
    query: ["document_id"],
    body: true,
    maxRequestBytes: MAX_KNOWLEDGE_REQUEST_BYTES,
  });
}
