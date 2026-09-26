import React from 'react';
import { HugeiconsIcon } from '@hugeicons/react';
import {
  Mic01Icon,
  MicOff01Icon,
  CallEnd01Icon,
  VolumeHighIcon,
  VolumeMute01Icon,
  SparklesIcon,
  SentIcon,
  BotIcon,
  UserIcon,
  Delete02Icon,
  StopIcon,
  PlayIcon,
  PauseIcon,
  RotateLeft01Icon,
  CheckmarkCircle02Icon,
  AlertCircleIcon,
  Alert02Icon,
  Cancel01Icon,
  RadioIcon,
  ArrowRight01Icon,
  HeadphonesIcon,
  SlidersHorizontalIcon,
  Globe02Icon,
  ServerIcon,
  FlashIcon,
  Key01Icon,
  ShieldCheckIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  Wifi01Icon,
} from '@hugeicons/core-free-icons';

/**
 * Higher-order component to generate standard icon elements powered by @hugeicons/react.
 * Compatible with standard props: size, color, strokeWidth, className, style.
 */
function createIcon(iconDef, defaultStrokeWidth = 1.7) {
  const IconComponent = ({
    size = 20,
    color = 'currentColor',
    strokeWidth = defaultStrokeWidth,
    className,
    style,
    ...rest
  }) => {
    return (
      <HugeiconsIcon
        icon={iconDef}
        size={size}
        color={color}
        strokeWidth={strokeWidth}
        className={className}
        style={style}
        {...rest}
      />
    );
  };
  IconComponent.displayName = `Hugeicon_${iconDef.name || 'Icon'}`;
  return IconComponent;
}

export const Mic = createIcon(Mic01Icon);
export const MicOff = createIcon(MicOff01Icon);
export const PhoneOff = createIcon(CallEnd01Icon);
export const Volume2 = createIcon(VolumeHighIcon);
export const VolumeX = createIcon(VolumeMute01Icon);
export const Sparkles = createIcon(SparklesIcon);
export const Send = createIcon(SentIcon);
export const Bot = createIcon(BotIcon);
export const User = createIcon(UserIcon);
export const Trash2 = createIcon(Delete02Icon);
export const Square = createIcon(StopIcon);
export const Play = createIcon(PlayIcon);
export const Pause = createIcon(PauseIcon);
export const RotateCcw = createIcon(RotateLeft01Icon);
export const CheckCircle2 = createIcon(CheckmarkCircle02Icon);
export const AlertCircle = createIcon(AlertCircleIcon);
export const AlertTriangle = createIcon(Alert02Icon);
export const X = createIcon(Cancel01Icon);
export const Radio = createIcon(RadioIcon);
export const ArrowRight = createIcon(ArrowRight01Icon);
export const Headphones = createIcon(HeadphonesIcon);
export const Sliders = createIcon(SlidersHorizontalIcon);
export const Globe = createIcon(Globe02Icon);
export const Server = createIcon(ServerIcon);
export const Zap = createIcon(FlashIcon);
export const Key = createIcon(Key01Icon);
export const ShieldCheck = createIcon(ShieldCheckIcon);
export const ChevronDown = createIcon(ChevronDownIcon);
export const ChevronUp = createIcon(ChevronUpIcon);
export const Wifi = createIcon(Wifi01Icon);

export { HugeiconsIcon };
