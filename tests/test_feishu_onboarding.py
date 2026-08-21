from interview_agent.feishu.onboarding import (
    ASK_COMPANY, ASK_POSITION, ASK_RESUME, OnboardingFlow,
)


def test_opening_question():
    assert OnboardingFlow().opening_question == ASK_COMPANY


def test_full_path_company_position_resume():
    f = OnboardingFlow()
    replies, r1 = f.feed("字节")
    assert replies == [ASK_POSITION] and r1 is None
    replies, r2 = f.feed("后端开发")
    assert replies == [ASK_RESUME] and r2 is None
    replies, done = f.feed("熟悉 Python")
    assert replies == [] and done is None
    replies, done = f.feed("做过订单系统")
    assert replies == [] and done is None
    replies, done = f.feed("END")
    assert done is not None
    assert done.company == "字节"
    assert done.position == "后端开发"
    assert done.resume_text == "熟悉 Python\n做过订单系统"


def test_skip_everything():
    f = OnboardingFlow()
    f.feed("跳过")
    f.feed("跳过")
    replies, done = f.feed("跳过")
    assert done is not None
    assert done.company == "" and done.position == "" and done.resume_text == ""
    assert replies  # 有确认文案


def test_end_inside_single_message_truncates():
    f = OnboardingFlow()
    f.feed("字节")
    f.feed("后端开发")
    _, done = f.feed("第一行\n第二行\nEND\n不该出现")
    assert done is not None
    assert done.resume_text == "第一行\n第二行"


def test_end_as_first_resume_message_is_no_resume_mode():
    f = OnboardingFlow()
    f.feed("跳过")
    f.feed("跳过")
    _, done = f.feed("END")
    assert done is not None and done.resume_text == ""
