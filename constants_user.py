# constants_user.py
# Personal overrides for constants.py.
#
# Copy any constant from constants.py into this file and change its value.
# This file is loaded last, so your definitions take priority over the
# script defaults.  Keep this file when updating the script — drop in the
# new versions of all other files and your customisations stay intact.
#
# Example — swap the Note-mode lock pulse colour to orange:
#   NOTE_LOCK_PULSE_RGB = (63, 20, 0)
#
# Example — change the default scale:
#   DEFAULT_STATE = {**DEFAULT_STATE, "scale_index": 3}
#   (import DEFAULT_STATE from constants first if you do this)



# MK1-protocol "set duty cycle" (brightness) command, sent once on init.
# Brightness = numerator / denominator; hardware default is 1/5.
# Valid range per the Launchpad S PRM: numerator 1-18, denominator 3-18.
# Only values SMALLER than 1/2 seem accepted by the Launchpad S. However
# 1/11 and smaller are declared "absolutely revolting" by Novation 
# due to flickering issues.
MK1_DUTY_CYCLE_NUMERATOR   = 7
MK1_DUTY_CYCLE_DENOMINATOR = 15