<?php

/**
 * Injects the chart-bootstrap JS + CSS into OpenEMR page heads so the
 * Co-Pilot drawer + floating button are available on patient chart pages.
 *
 * Hooks into:
 *   - ScriptFilterEvent (`html.head.script.filter`) — adds chart-bootstrap.js
 *   - StyleFilterEvent  (`html.head.style.filter`)  — adds chart-bootstrap.css
 *
 * Both files are filtered through `ModulesApplication::filterSafeLocalModuleFiles`
 * which only allows real local files under the modules root.
 *
 * The subscriber injects unconditionally (every page); chart-bootstrap.js
 * self-determines whether to render the drawer (only on patient chart
 * pages, idempotent across iframes via the top-window check).
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot\EventSubscriber;

use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Events\Core\ScriptFilterEvent;
use OpenEMR\Events\Core\StyleFilterEvent;
use Symfony\Component\EventDispatcher\EventSubscriberInterface;

final class ScriptFilterSubscriber implements EventSubscriberInterface
{
    private const MODULE_PUBLIC_PATH =
        '/interface/modules/custom_modules/oe-module-clinical-copilot/public';

    public static function getSubscribedEvents(): array
    {
        return [
            ScriptFilterEvent::EVENT_NAME => 'onScriptFilter',
            StyleFilterEvent::EVENT_NAME => 'onStyleFilter',
        ];
    }

    public function onScriptFilter(ScriptFilterEvent $event): void
    {
        $scripts = $event->getScripts();
        $scripts[] = OEGlobalsBag::getInstance()->getWebRoot()
            . self::MODULE_PUBLIC_PATH . '/chart-bootstrap.js';
        $event->setScripts($scripts);
    }

    public function onStyleFilter(StyleFilterEvent $event): void
    {
        $styles = $event->getStyles();
        $styles[] = OEGlobalsBag::getInstance()->getWebRoot()
            . self::MODULE_PUBLIC_PATH . '/chart-bootstrap.css';
        $event->setStyles($styles);
    }
}
