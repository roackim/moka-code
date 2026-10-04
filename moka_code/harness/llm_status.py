from enum import Enum, auto

class AgentState(Enum):
    IDLE = auto()
    THINKING = auto()
    ANSWERING = auto()
