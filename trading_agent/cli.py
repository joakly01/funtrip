import argparse
import json
import sys

from .common import DomainError
from .service import Service


def main():
    p=argparse.ArgumentParser(description="私人交易伙伴工具。用户自然语言入口为 Codex 项目对话。")
    p.add_argument("--data-dir",help="隔离资料目录；默认项目 data/trading")
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("status")
    sub.add_parser("tools")
    sub.add_parser("mcp")
    call=sub.add_parser("call")
    call.add_argument("tool")
    call.add_argument("--input",default="-",help="UTF-8 JSON 文件路径；默认从 stdin 读取")
    approve=sub.add_parser("approve-rule",help="仅在用户明确确认具体提案后人工调用，不作为 MCP 工具")
    approve.add_argument("proposal_id");approve.add_argument("digest");approve.add_argument("confirmation_source_id")
    args=p.parse_args()
    service=Service(args.data_dir)
    if args.command=="mcp":
        from .mcp import run
        run(service);return
    if args.command=="status": output=service.call("agent_status",{})
    elif args.command=="tools": output=list(service.specs.values())
    elif args.command=="approve-rule":
        try: output={"status":"ok","data":service.store.activate(args.proposal_id,args.digest,args.confirmation_source_id)}
        except DomainError as e: output={"status":"error","error":{"code":e.code,"message":e.message}}
    else:
        try:
            if args.input=="-": arguments=json.load(sys.stdin)
            else:
                with open(args.input,encoding="utf-8") as f: arguments=json.load(f)
            output=service.call(args.tool,arguments)
        except (ValueError,OSError) as e:
            output={"status":"error","error":{"code":"INPUT_ERROR","message":type(e).__name__}}
    print(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False))
    if isinstance(output,dict) and output.get("status")=="error": raise SystemExit(1)


if __name__=="__main__":main()
