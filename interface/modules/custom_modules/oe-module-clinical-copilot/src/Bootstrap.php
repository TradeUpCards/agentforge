<?php

/**
 * Bootstrap for the AgentForge Clinical Co-Pilot module.
 *
 * Wires the event subscriber that injects a "Clinical Co-Pilot" tab into
 * the patient chart menu. Configuration values (agent base URL, HMAC
 * secret) are read from environment variables so the same module can run
 * in dev and prod without code changes.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ClinicalCopilot;

use OpenEMR\Modules\ClinicalCopilot\EventSubscriber\PatientMenuSubscriber;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

final class Bootstrap
{
    public const MODULE_INSTALLATION_PATH = '/interface/modules/custom_modules/';
    public const MODULE_NAME = 'oe-module-clinical-copilot';

    /**
     * Default base URL for the Python agent service.
     *
     * Inside the deployed Docker stack the agent is reachable at
     * `http://agent:8000`. For local dev this can be overridden via the
     * `AGENT_BASE_URL` environment variable (e.g. `http://host.docker.internal:8000`).
     */
    public const DEFAULT_AGENT_BASE_URL = 'http://agent:8000';

    public function __construct(
        private readonly EventDispatcherInterface $eventDispatcher,
    ) {
    }

    public function subscribeToEvents(): void
    {
        $this->eventDispatcher->addSubscriber(new PatientMenuSubscriber());
    }

    /**
     * Resolve the configured agent base URL.
     *
     * Falls back to the in-stack hostname when no override is supplied.
     */
    public static function getAgentBaseUrl(): string
    {
        $value = getenv('AGENT_BASE_URL');
        if (is_string($value) && $value !== '') {
            return rtrim($value, '/');
        }

        return self::DEFAULT_AGENT_BASE_URL;
    }

    /**
     * Resolve the HMAC secret shared with the Python agent service.
     *
     * Returns null when the secret is not configured; callers must treat
     * that as a hard failure.
     */
    public static function getHmacSecret(): ?string
    {
        $value = getenv('OPENEMR_HMAC_SECRET');
        if (is_string($value) && $value !== '') {
            return $value;
        }

        return null;
    }
}
