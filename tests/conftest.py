import os
import sys

# Make "import shout_lan" work when running pytest from the project folder.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
