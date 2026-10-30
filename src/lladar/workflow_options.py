"""Side-effect-free option checks shared by CLI execution and plan compilation."""


def validate_workflow_options(args):
    if args.command != "run-agent":
        return
    if args.api_guide is not None and (not args.service_url or args.situation_config is not None):
        raise ValueError('--api-guide requires --service-url in single-question mode')
    if args.export_adapter is not None and ((args.project is None and args.api_guide is None) or not args.service_url
            or args.page_url is not None or args.situation_config is not None):
        raise ValueError('--export-adapter requires --service-url and explicit --project or --api-guide in single-question mode')
    if args.adapt is not None and args.project is None and args.page_url is None and args.api_guide is None:
        raise ValueError('--adapt requires an explicit --project, --api-guide or --page-url')
    if args.demo_question is not None and (args.page_url is None or not args.demo_question.strip()):
        raise ValueError('--demo-question requires --page-url and a non-empty question')
    if args.adapter is not None and args.page_url is None:
        raise ValueError('--adapter requires --page-url')
    if args.adapt is not None and args.page_url is not None:
        raise ValueError('--adapt is project mode only; use --adapter with --page-url')
    if args.situation_config:
        if args.dataset is not None or args.skill is not None:
            raise ValueError('--situation-config cannot be combined with DATASET or --skill')
        if args.num_scenarios is None:
            raise ValueError('--num-scenarios is required with --situation-config')
        if args.page_url is not None and args.conversation_policy != 'independent':
            raise ValueError('website situation requires --conversation-policy independent')
        if args.page_url is None and any((args.confirm_browser_run, args.fresh_browser_profile, args.allow_response_model_transfer)):
            raise ValueError('browser options do not apply to situation mode')
        if args.page_url is not None and args.service_url is not None:
            raise ValueError('--service-url cannot be combined with --page-url')
    else:
        if args.dataset is None:
            raise ValueError('DATASET is required unless --situation-config is set')
        if args.num_scenarios is not None:
            raise ValueError('--num-scenarios requires --situation-config')
