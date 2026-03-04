use anchor_lang::prelude::*;
use anchor_spl::token::{Token, TokenAccount, Mint};
use crate::state::*;
use crate::errors::ContractError;

#[derive(Accounts)]
pub struct InitConfig<'info> {
    #[account(
        init,
        seeds = [b"config"],
        bump,
        payer = admin,
        space = Config::SPACE
    )]
    pub config: Account<'info, Config>,
    #[account(mut)]
    pub admin: Signer<'info>,
    pub skr_mint: Account<'info, Mint>,
    /// CHECK: Can be any valid system account or PDA to receive fees
    pub treasury_wallet: UncheckedAccount<'info>,
    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
}

pub fn init_config(
    ctx: Context<InitConfig>,
    min_bet: u64,
    max_bet: u64,
) -> Result<()> {
    require!(min_bet <= max_bet, ContractError::InvalidBetRange);
    require!(ctx.accounts.skr_mint.decimals == 6, ContractError::TokenMintMismatch);

    let config = &mut ctx.accounts.config;
    config.admin = ctx.accounts.admin.key();
    config.skr_mint = ctx.accounts.skr_mint.key();
    config.treasury_wallet = ctx.accounts.treasury_wallet.key();
    config.fee_bps = 500; // Default 5%
    config.min_bet = min_bet;
    config.max_bet = max_bet;
    config.match_counter = 0;
    config.paused = false;

    Ok(())
}

#[derive(Accounts)]
pub struct UpdateConfig<'info> {
    #[account(
        mut,
        seeds = [b"config"],
        bump,
        has_one = admin @ ContractError::Unauthorized
    )]
    pub config: Account<'info, Config>,
    pub admin: Signer<'info>,
}

pub fn update_config(
    ctx: Context<UpdateConfig>,
    new_admin: Option<Pubkey>,
    new_treasury_wallet: Option<Pubkey>,
    new_fee_bps: Option<u16>,
    new_min_bet: Option<u64>,
    new_max_bet: Option<u64>,
) -> Result<()> {
    let config = &mut ctx.accounts.config;

    // Validate fee
    if let Some(fee) = new_fee_bps {
        require!(fee <= 1000, ContractError::FeeBpsOutOfRange);
        config.fee_bps = fee;
    }

    // Validate min/max bets
    let final_min_bet = new_min_bet.unwrap_or(config.min_bet);
    let final_max_bet = new_max_bet.unwrap_or(config.max_bet);
    require!(final_min_bet <= final_max_bet, ContractError::InvalidBetRange);
    
    config.min_bet = final_min_bet;
    config.max_bet = final_max_bet;

    // Apply unstructured updates
    if let Some(admin_key) = new_admin {
        config.admin = admin_key;
    }
    if let Some(wallet) = new_treasury_wallet {
        config.treasury_wallet = wallet;
    }

    Ok(())
}

#[derive(Accounts)]
pub struct SetPaused<'info> {
    #[account(
        mut,
        seeds = [b"config"],
        bump,
        has_one = admin @ ContractError::Unauthorized
    )]
    pub config: Account<'info, Config>,
    pub admin: Signer<'info>,
}

pub fn set_paused(ctx: Context<SetPaused>, paused: bool) -> Result<()> {
    ctx.accounts.config.paused = paused;
    Ok(())
}

// Match, Lock, Resolve, Cancel instructions below:

#[derive(Accounts)]
pub struct CreateMatch<'info> {
    #[account(
        mut,
        seeds = [b"config"],
        bump,
        has_one = admin @ ContractError::Unauthorized
    )]
    pub config: Account<'info, Config>,
    
    #[account(
        init,
        seeds = [b"match", config.match_counter.to_le_bytes().as_ref()],
        bump,
        payer = admin,
        space = Match::SPACE
    )]
    pub match_account: Account<'info, Match>,

    /// CHECK: PDA derived purely to own the Vault ATA
    #[account(
        seeds = [b"vault_auth", match_account.key().as_ref()],
        bump
    )]
    pub vault_authority: UncheckedAccount<'info>,

    #[account(
        init,
        payer = admin,
        associated_token::mint = skr_mint,
        associated_token::authority = vault_authority
    )]
    pub vault_ata: Account<'info, TokenAccount>,
    
    pub skr_mint: Account<'info, Mint>,

    #[account(mut)]
    pub admin: Signer<'info>,
    
    pub token_program: Program<'info, Token>,
    pub associated_token_program: Program<'info, anchor_spl::associated_token::AssociatedToken>,
    pub system_program: Program<'info, System>,
    pub rent: Sysvar<'info, Rent>,
}

pub fn create_match(
    ctx: Context<CreateMatch>,
    model_a_hash: [u8; 32],
    model_b_hash: [u8; 32],
) -> Result<()> {
    let config = &mut ctx.accounts.config;
    
    // Check if paused (optional per spec, but good practice). We choose to allow match creation while paused.
    
    let match_account = &mut ctx.accounts.match_account;
    
    match_account.id = config.match_counter;
    match_account.status = MatchStatus::Open;
    match_account.model_a_hash = model_a_hash;
    match_account.model_b_hash = model_b_hash;
    match_account.total_a = 0;
    match_account.total_b = 0;
    match_account.winner = WinnerSide::None;
    match_account.fee_amount = 0;
    match_account.payout_pool = 0;
    match_account.winning_total = 0;
    match_account.claimed_winning_total = 0;
    match_account.refunded_total = 0;
    match_account.vault_authority = ctx.accounts.vault_authority.key();
    match_account.vault_ata = ctx.accounts.vault_ata.key();
    match_account.created_at = Clock::get()?.unix_timestamp;
    match_account.locked_at = i64::MIN;
    match_account.resolved_at = i64::MIN;

    // Increment global match counter
    config.match_counter = config.match_counter.checked_add(1).unwrap();

    Ok(())
}

#[derive(Accounts)]
pub struct LockMatch<'info> {
    #[account(
        seeds = [b"config"],
        bump,
        has_one = admin @ ContractError::Unauthorized
    )]
    pub config: Account<'info, Config>,

    #[account(mut)]
    pub match_account: Account<'info, Match>,

    pub admin: Signer<'info>,
}

pub fn lock_match(ctx: Context<LockMatch>) -> Result<()> {
    let match_account = &mut ctx.accounts.match_account;
    
    require!(match_account.status == MatchStatus::Open, ContractError::MatchNotOpen);

    match_account.status = MatchStatus::Locked;
    match_account.locked_at = Clock::get()?.unix_timestamp;

    Ok(())
}


#[derive(Accounts)]
pub struct ResolveMatch<'info> {
    #[account(
        seeds = [b"config"],
        bump,
        has_one = admin @ ContractError::Unauthorized,
        has_one = skr_mint @ ContractError::TokenMintMismatch
    )]
    pub config: Account<'info, Config>,

    #[account(mut)]
    pub match_account: Account<'info, Match>,

    #[account(
        mut,
        associated_token::mint = skr_mint,
        associated_token::authority = vault_authority
    )]
    pub vault_ata: Account<'info, TokenAccount>,
    
    /// CHECK: PDA derived purely to own the Vault ATA and sign CPIs
    #[account(
        mut,
        seeds = [b"vault_auth", match_account.key().as_ref()],
        bump
    )]
    pub vault_authority: UncheckedAccount<'info>,

    #[account(
        mut,
        token::mint = skr_mint
    )]
    pub treasury_ata: Account<'info, TokenAccount>,
    
    pub skr_mint: Account<'info, Mint>,

    #[account(mut)]
    pub admin: Signer<'info>,

    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
}

pub fn resolve_match(ctx: Context<ResolveMatch>, winner_side: WinnerSide) -> Result<()> {
    // 1. Validations
    let match_account = &mut ctx.accounts.match_account;
    let config = &ctx.accounts.config;
    let expected_treasury = anchor_spl::associated_token::get_associated_token_address(&config.treasury_wallet, &config.skr_mint);
    
    require!(ctx.accounts.treasury_ata.key() == expected_treasury, ContractError::Unauthorized);
    require!(match_account.status == MatchStatus::Locked, ContractError::MatchNotLocked);
    require!(winner_side == WinnerSide::A || winner_side == WinnerSide::B, ContractError::InvalidSide);
    require!(match_account.vault_ata == ctx.accounts.vault_ata.key(), ContractError::VaultAuthorityMismatch);
    require!(match_account.vault_authority == ctx.accounts.vault_authority.key(), ContractError::VaultAuthorityMismatch);

    // 2. Compute Pool and Fees
    let total_a = match_account.total_a;
    let total_b = match_account.total_b;
    let pool = total_a.checked_add(total_b).unwrap();
    
    let fee = ((pool as u128)
        .checked_mul(config.fee_bps as u128).unwrap()
        .checked_div(10_000).unwrap()) as u64;

    let payout_pool = pool.checked_sub(fee).unwrap();
    
    let winning_total = match winner_side {
        WinnerSide::A => total_a,
        WinnerSide::B => total_b,
        _ => 0,
    };

    // 3. Write state
    match_account.winner = winner_side.clone();
    match_account.fee_amount = fee;
    match_account.payout_pool = payout_pool;
    match_account.winning_total = winning_total;
    match_account.resolved_at = Clock::get()?.unix_timestamp;
    match_account.status = MatchStatus::Resolved;

    let match_key = match_account.key();
    let signer_seeds: &[&[&[u8]]] = &[&[
        b"vault_auth",
        match_key.as_ref(),
        &[ctx.bumps.vault_authority],
    ]];

    // 4. Branch A: Pool == 0 (no bets at all)
    if pool == 0 {
        // Vault is empty. Instantly close ATA and Match PDA, rent to Admin.
        let cpi_close_accounts = anchor_spl::token::CloseAccount {
            account: ctx.accounts.vault_ata.to_account_info(),
            destination: ctx.accounts.admin.to_account_info(),
            authority: ctx.accounts.vault_authority.to_account_info(),
        };
        let cpi_close_ctx = CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            cpi_close_accounts,
            signer_seeds,
        );
        anchor_spl::token::close_account(cpi_close_ctx)?;

        // Close Match PDA safely via Anchor's .close() method
        match_account.close(ctx.accounts.admin.to_account_info())?;
        
        return Ok(());
    }

    // 5. Branch B: Pool > 0 && Winning Total == 0 (all bets lost)
    if winning_total == 0 {
        // Swift justice. All fee + payout goes to treasury.
        let sweep_amount = fee.checked_add(payout_pool).unwrap();
        
        let cpi_transfer_accounts = anchor_spl::token::Transfer {
            from: ctx.accounts.vault_ata.to_account_info(),
            to: ctx.accounts.treasury_ata.to_account_info(),
            authority: ctx.accounts.vault_authority.to_account_info(),
        };
        let cpi_transfer_ctx = CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            cpi_transfer_accounts,
            signer_seeds,
        );
        anchor_spl::token::transfer(cpi_transfer_ctx, sweep_amount)?;

        // Now vault is empty (minus dust), close ATA and Match to Admin
        let cpi_close_accounts = anchor_spl::token::CloseAccount {
            account: ctx.accounts.vault_ata.to_account_info(),
            destination: ctx.accounts.admin.to_account_info(),
            authority: ctx.accounts.vault_authority.to_account_info(),
        };
        let cpi_close_ctx = CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            cpi_close_accounts,
            signer_seeds,
        );
        anchor_spl::token::close_account(cpi_close_ctx)?;

        // Close Match PDA safely
        match_account.close(ctx.accounts.admin.to_account_info())?;
        
        return Ok(());
    }

    // 6. Branch C: Normal case (winning_total > 0)
    // Transfer the 5% fee to the treasury immediately.
    if fee > 0 {
        let cpi_transfer_accounts = anchor_spl::token::Transfer {
            from: ctx.accounts.vault_ata.to_account_info(),
            to: ctx.accounts.treasury_ata.to_account_info(),
            authority: ctx.accounts.vault_authority.to_account_info(),
        };
        let cpi_transfer_ctx = CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            cpi_transfer_accounts,
            signer_seeds,
        );
        anchor_spl::token::transfer(cpi_transfer_ctx, fee)?;
    }

    Ok(())
}

#[derive(Accounts)]
pub struct CancelMatch<'info> {
    #[account(
        seeds = [b"config"],
        bump,
        has_one = admin @ ContractError::Unauthorized
    )]
    pub config: Account<'info, Config>,

    #[account(mut)]
    pub match_account: Account<'info, Match>,

    #[account(
        mut,
        associated_token::mint = skr_mint,
        associated_token::authority = vault_authority
    )]
    pub vault_ata: Account<'info, TokenAccount>,
    
    /// CHECK: PDA derived purely to own the Vault ATA and sign CPIs
    #[account(
        mut,
        seeds = [b"vault_auth", match_account.key().as_ref()],
        bump
    )]
    pub vault_authority: UncheckedAccount<'info>,

    pub skr_mint: Account<'info, Mint>,

    #[account(mut)]
    pub admin: Signer<'info>,

    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
}

pub fn cancel_match(ctx: Context<CancelMatch>) -> Result<()> {
    let match_account = &mut ctx.accounts.match_account;
    
    require!(
        match_account.status == MatchStatus::Open || match_account.status == MatchStatus::Locked,
        ContractError::MatchNotCancellable
    );

    match_account.status = MatchStatus::Cancelled;
    
    let total_bets = match_account.total_a.checked_add(match_account.total_b).unwrap();
    
    if total_bets == 0 {
        // Immediate close if nobody bet
        let match_key = match_account.key();
        let signer_seeds: &[&[&[u8]]] = &[&[
            b"vault_auth",
            match_key.as_ref(),
            &[ctx.bumps.vault_authority],
        ]];

        let cpi_close_accounts = anchor_spl::token::CloseAccount {
            account: ctx.accounts.vault_ata.to_account_info(),
            destination: ctx.accounts.admin.to_account_info(),
            authority: ctx.accounts.vault_authority.to_account_info(),
        };
        let cpi_close_ctx = CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            cpi_close_accounts,
            signer_seeds,
        );
        anchor_spl::token::close_account(cpi_close_ctx)?;

        // Close Match PDA safely
        match_account.close(ctx.accounts.admin.to_account_info())?;
    }
    // Else, leave open on-chain for bettors to individually call refund_bet

    Ok(())
}
