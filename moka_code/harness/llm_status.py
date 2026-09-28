from enum import Enum, auto

class AgentState(Enum):
    UNCONNECTED = auto()
    IDLE = auto()
    THINKING = auto()
    ANSWERING = auto()
