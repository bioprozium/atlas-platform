"use strict";

const fs = require("node:fs");
const validator = require("gltf-validator");

const supported = new Set(
    validator.supportedExtensions().filter((name) => name === "KHR_materials_unlit")
);

function issue(code, pointer, message) {
    return {code, pointer, message, severity: 0};
}

function reportText(value, length) {
    return value?.slice(0, length).toWellFormed().replaceAll("\0", "\uFFFD");
}

async function inspect(bytes) {
    const length = bytes.readUInt32LE(12);
    let document;
    try {
        document = JSON.parse(new TextDecoder("utf-8", {fatal: true, ignoreBOM: true})
            .decode(bytes.subarray(20, 20 + length)));
    } catch {
        return {messages: [issue("GLTF_JSON_INVALID", "/",
            "The JSON chunk must contain valid UTF-8 glTF JSON.")]};
    }

    const policyIssues = [];
    for (const collection of ["buffers", "images"]) {
        const items = document?.[collection];
        if (!Array.isArray(items)) continue;
        for (const [index, item] of items.entries()) {
            if (typeof item?.uri === "string" && !/^data:/i.test(item.uri)) {
                policyIssues.push(issue("EXTERNAL_RESOURCE", "/" + collection + "/" + index + "/uri",
                    "Atlas requires embedded resources."));
            }
            if (policyIssues.length === 100) break;
        }
        if (policyIssues.length === 100) break;
    }

    if (Array.isArray(document?.extensionsRequired)) {
        for (const [index, name] of document.extensionsRequired.entries()) {
            if (policyIssues.length === 100) break;
            if (typeof name === "string" && !supported.has(name)) {
                policyIssues.push(issue("REQUIRED_EXTENSION_UNSUPPORTED", "/extensionsRequired/" + index,
                    "The required extension is outside the Atlas ingestion profile."));
            }
        }
    }
    if (policyIssues.length) return {messages: policyIssues, truncated: policyIssues.length === 100};

    let externalRequest = false;
    const result = await validator.validateBytes(bytes, {
        format: "glb",
        maxIssues: 100,
        writeTimestamp: false,
        externalResourceFunction: () => {
            externalRequest = true;
            return Promise.reject(new Error("External resource access is disabled."));
        },
    });
    const {issues, info} = result;
    const messages = issues.messages.map(({code, severity, message, pointer, offset}) => ({
        code, severity, message: reportText(message, 1024), pointer: reportText(pointer, 512), offset,
    }));
    if (externalRequest) {
        messages.push(issue("EXTERNAL_RESOURCE", "/", "The validator requested an external resource."));
    }
    // Keep useful counts and omit arbitrary source strings, generator data and resource URIs.
    const summary = {};
    for (const field of ["animationCount", "materialCount", "drawCallCount", "totalVertexCount",
        "totalTriangleCount", "maxUVs", "maxInfluences", "hasTextures", "hasSkins"]) {
        if (info?.[field] !== undefined) summary[field] = info[field];
    }
    return {messages, numErrors: issues.numErrors, truncated: issues.truncated, summary};
}

async function main() {
    const version = validator.version();
    if (!process.versions.node.startsWith("24.") || version !== process.argv[2]) {
        throw new Error("Unexpected Node.js or validator version.");
    }
    const result = await inspect(fs.readFileSync(0));
    process.stdout.write(JSON.stringify({version, ...result}));
}

main().catch(() => { process.exitCode = 1; });
