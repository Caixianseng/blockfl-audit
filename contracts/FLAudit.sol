// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";

/// @notice A teaching audit ledger: commitments do not prove honest training.
contract FLAudit is AccessControl {
    bytes32 public constant AGGREGATOR_ROLE = keccak256("AGGREGATOR_ROLE");
    bytes32 public constant PARTICIPANT_ROLE = keccak256("PARTICIPANT_ROLE");
    uint256 public constant CLIENT_COUNT = 4;

    struct Run {
        address aggregator;
        uint256 currentRound;
        bool roundOpen;
        address[] participants;
    }

    mapping(bytes32 => Run) private runs;
    mapping(bytes32 => mapping(address => bool)) public runParticipants;
    mapping(bytes32 => mapping(uint256 => mapping(address => bytes32))) public updateHashes;
    mapping(bytes32 => mapping(uint256 => uint256)) public submissionCounts;
    mapping(bytes32 => mapping(uint256 => bytes32)) public aggregateHashes;

    error InvalidParticipants();
    error RunAlreadyExists();
    error UnknownRun();
    error NotRunAggregator();
    error InvalidRound();
    error RoundAlreadyOpen();
    error RoundNotOpen();
    error NotRunParticipant();
    error DuplicateSubmission();
    error EmptyHash();
    error MissingSubmissions();

    event RunCreated(bytes32 indexed runId, address indexed aggregator, address[] participants);
    event RoundOpened(bytes32 indexed runId, uint256 indexed round);
    event UpdateCommitted(bytes32 indexed runId, uint256 indexed round, address indexed participant, bytes32 updateHash);
    event AggregateCommitted(bytes32 indexed runId, uint256 indexed round, bytes32 aggregateHash);

    constructor(address administrator, address[] memory approvedParticipants) {
        require(administrator != address(0), "zero administrator");
        _grantRole(DEFAULT_ADMIN_ROLE, administrator);
        _grantRole(AGGREGATOR_ROLE, administrator);
        for (uint256 i; i < approvedParticipants.length; ++i) {
            require(approvedParticipants[i] != address(0), "zero participant");
            _grantRole(PARTICIPANT_ROLE, approvedParticipants[i]);
        }
    }

    function createRun(bytes32 runId, address[] calldata participants) external onlyRole(AGGREGATOR_ROLE) {
        if (runs[runId].aggregator != address(0)) revert RunAlreadyExists();
        if (runId == bytes32(0) || participants.length != CLIENT_COUNT) revert InvalidParticipants();
        Run storage run = runs[runId];
        run.aggregator = msg.sender;
        for (uint256 i; i < participants.length; ++i) {
            address participant = participants[i];
            if (!hasRole(PARTICIPANT_ROLE, participant) || runParticipants[runId][participant]) revert InvalidParticipants();
            runParticipants[runId][participant] = true;
            run.participants.push(participant);
        }
        emit RunCreated(runId, msg.sender, participants);
    }

    function beginRound(bytes32 runId, uint256 round) external onlyRole(AGGREGATOR_ROLE) {
        Run storage run = runs[runId];
        _checkAggregator(run);
        if (run.roundOpen) revert RoundAlreadyOpen();
        if (round != run.currentRound + 1) revert InvalidRound();
        run.currentRound = round;
        run.roundOpen = true;
        emit RoundOpened(runId, round);
    }

    function submitUpdate(bytes32 runId, uint256 round, bytes32 updateHash) external onlyRole(PARTICIPANT_ROLE) {
        Run storage run = runs[runId];
        if (run.aggregator == address(0)) revert UnknownRun();
        if (!run.roundOpen) revert RoundNotOpen();
        if (round != run.currentRound) revert InvalidRound();
        if (!runParticipants[runId][msg.sender]) revert NotRunParticipant();
        if (updateHash == bytes32(0)) revert EmptyHash();
        if (updateHashes[runId][round][msg.sender] != bytes32(0)) revert DuplicateSubmission();
        updateHashes[runId][round][msg.sender] = updateHash;
        ++submissionCounts[runId][round];
        emit UpdateCommitted(runId, round, msg.sender, updateHash);
    }

    function commitAggregate(bytes32 runId, uint256 round, bytes32 aggregateHash) external onlyRole(AGGREGATOR_ROLE) {
        Run storage run = runs[runId];
        _checkAggregator(run);
        if (!run.roundOpen) revert RoundNotOpen();
        if (round != run.currentRound) revert InvalidRound();
        if (aggregateHash == bytes32(0)) revert EmptyHash();
        if (submissionCounts[runId][round] != CLIENT_COUNT) revert MissingSubmissions();
        aggregateHashes[runId][round] = aggregateHash;
        run.roundOpen = false;
        emit AggregateCommitted(runId, round, aggregateHash);
    }

    function getRun(bytes32 runId) external view returns (address aggregator, uint256 currentRound, bool roundOpen, address[] memory participants) {
        Run storage run = runs[runId];
        return (run.aggregator, run.currentRound, run.roundOpen, run.participants);
    }

    function _checkAggregator(Run storage run) private view {
        if (run.aggregator == address(0)) revert UnknownRun();
        if (msg.sender != run.aggregator) revert NotRunAggregator();
    }
}
