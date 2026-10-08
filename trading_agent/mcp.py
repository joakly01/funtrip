"""Minimal stdio MCP transport: tools only, no remote network listener."""
import json
import sys


def run(service, source=None, sink=None):
    source,sink=source or sys.stdin,sink or sys.stdout
    for line in source:
        if not line.strip(): continue
        ident=None
        try:
            request=json.loads(line)
            if not isinstance(request,dict): raise ValueError("object expected")
            ident=request.get("id")
            method=request.get("method")
            if ident is None: continue
            params=request.get("params") or {}
            if method=="initialize":
                supported={"2024-11-05","2025-03-26","2025-06-18"}
                requested=params.get("protocolVersion")
                result={"protocolVersion":requested if requested in supported else "2025-06-18", "capabilities":{"tools":{"listChanged":False}},
                        "serverInfo":{"name":"private-trading-partner","version":"0.2.0"},
                        "instructions":"读取项目交易技能；使用原始证据，不把工具成功等同预测正确。规则激活不暴露为工具。"}
            elif method=="ping": result={}
            elif method=="tools/list": result={"tools":list(service.specs.values())}
            elif method=="tools/call":
                output=service.call(params.get("name"),params.get("arguments",{}))
                result={"content":[{"type":"text","text":json.dumps(output,ensure_ascii=False,allow_nan=False)}],"structuredContent":output,"isError":output["status"]=="error"}
            else:
                sink.write(json.dumps({"jsonrpc":"2.0","id":ident,"error":{"code":-32601,"message":"Method not found"}})+"\n");sink.flush();continue
            response={"jsonrpc":"2.0","id":ident,"result":result}
        except (ValueError,TypeError,AttributeError):
            response={"jsonrpc":"2.0","id":ident,"error":{"code":-32700,"message":"Invalid JSON-RPC request"}}
        sink.write(json.dumps(response,ensure_ascii=False,allow_nan=False)+"\n")
        sink.flush()
